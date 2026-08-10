package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/types"
)

// safeMediaPath is the load-bearing guard for two invariants: it must reject
// path-traversal attempts, and it must give distinct messages distinct paths
// even when their stored filename collides (the bug that made the download
// cache return the wrong message's bytes).
func TestSafeMediaPath(t *testing.T) {
	chatDir := "store/chat"

	t.Run("rejects traversal in message ID", func(t *testing.T) {
		if _, err := safeMediaPath(chatDir, "../../etc/passwd", "a.ogg"); err == nil {
			t.Fatal("expected error for traversal in message ID")
		}
	})

	t.Run("rejects separator in filename", func(t *testing.T) {
		if _, err := safeMediaPath(chatDir, "ABC", "a/b.ogg"); err == nil {
			t.Fatal("expected error for separator in filename")
		}
	})

	for _, bad := range []string{"", ".", ".."} {
		t.Run("rejects component "+bad, func(t *testing.T) {
			if _, err := safeMediaPath(chatDir, bad, "a.ogg"); err == nil {
				t.Fatalf("expected error for message ID %q", bad)
			}
			if _, err := safeMediaPath(chatDir, "ABC", bad); err == nil {
				t.Fatalf("expected error for filename %q", bad)
			}
		})
	}

	t.Run("distinct IDs with same filename produce distinct paths", func(t *testing.T) {
		p1, err1 := safeMediaPath(chatDir, "MSG1", "audio_20260608.ogg")
		p2, err2 := safeMediaPath(chatDir, "MSG2", "audio_20260608.ogg")
		if err1 != nil || err2 != nil {
			t.Fatalf("unexpected error: %v %v", err1, err2)
		}
		if p1 == p2 {
			t.Fatalf("colliding filename produced identical paths: %s", p1)
		}
	})

	t.Run("valid path stays under chat dir", func(t *testing.T) {
		p, err := safeMediaPath(chatDir, "MSG1", "audio.ogg")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		abs, _ := filepath.Abs(chatDir)
		pabs, _ := filepath.Abs(p)
		if !strings.HasPrefix(pabs, abs) {
			t.Fatalf("path %s escaped chat dir %s", pabs, abs)
		}
	})
}

// extractDirectPathFromURL must keep the query string. whatsmeow's
// DownloadMediaWithPath concatenates "&hash=..." onto the direct path, so the
// query is what supplies the "?" and the CDN authorization token (oh) — drop it
// and every download 403s, which is exactly what happened to this bridge.
func TestExtractDirectPathFromURL(t *testing.T) {
	const url = "https://mmg.whatsapp.net/v/t62.7118-24/582729677_1690209148760939_n.enc" +
		"?ccb=11-4&oh=01_Q5Aa5QEFyuE2cc8EmroSnUoZk72zv970&oe=6AA1BE81&_nc_sid=5e03e0&mms3=true"

	got := extractDirectPathFromURL(url)

	if !strings.HasPrefix(got, "/v/t62.7118-24/") {
		t.Fatalf("direct path must start with the slashed path: got %q", got)
	}
	for _, param := range []string{"?ccb=11-4", "oh=01_Q5Aa5QEFyuE2cc8EmroSnUoZk72zv970", "oe=6AA1BE81"} {
		if !strings.Contains(got, param) {
			t.Errorf("direct path lost %q: got %q", param, got)
		}
	}

	// The invariant that broke: the URL whatsmeow builds on top of this must be
	// a single well-formed query, not a path with a stray '&'.
	built := "https://media.example.net" + got + "&hash=abc&mms-type=image&__wa-mms="
	if strings.Count(built, "?") != 1 {
		t.Fatalf("built URL must have exactly one '?': %s", built)
	}
	if strings.Index(built, "&") < strings.Index(built, "?") {
		t.Fatalf("built URL has a '&' before its '?': %s", built)
	}

	t.Run("unparseable URL is returned unchanged", func(t *testing.T) {
		if got := extractDirectPathFromURL("not-a-media-url"); got != "not-a-media-url" {
			t.Fatalf("expected the input back, got %q", got)
		}
	})
}

// normalizePhone must strip '+', spaces and '-' so contact resolution matches
// regardless of formatting (parity with the Python _normalize_phone).
func TestNormalizePhone(t *testing.T) {
	cases := map[string]string{
		"+55 62 99999-9999": "5562999999999",
		"5562999999999":     "5562999999999",
		"55-62-99999-9999":  "5562999999999",
		"  +5562 ":          "5562",
	}
	for in, want := range cases {
		if got := normalizePhone(in); got != want {
			t.Errorf("normalizePhone(%q) = %q, want %q", in, got, want)
		}
	}
}

// resolveContactJIDs must always return the PN JID first, even with a nil client
// (no LID store available), and must never panic on nil.
func TestResolveContactJIDsPNFirst(t *testing.T) {
	jids := resolveContactJIDs(nil, "+55 62 99999-9999")
	if len(jids) == 0 || jids[0] != "5562999999999@s.whatsapp.net" {
		t.Fatalf("expected PN JID first, got %v", jids)
	}
}

// actionSenderJID backs react/revoke sender resolution: fromMe must resolve to
// the local account (non-AD), never panic on a nil own JID, and fall back to
// chatJID (DM-correct) when fromMe is false.
func TestActionSenderJID(t *testing.T) {
	chatJID, err := types.ParseJID("5562999999999@s.whatsapp.net")
	if err != nil {
		t.Fatalf("unexpected error parsing chat JID: %v", err)
	}

	t.Run("fromMe true uses own JID non-AD", func(t *testing.T) {
		ownJID, err := types.ParseJID("5562988888888:1@s.whatsapp.net")
		if err != nil {
			t.Fatalf("unexpected error parsing own JID: %v", err)
		}
		got := actionSenderJID(&ownJID, chatJID, true)
		want := ownJID.ToNonAD()
		if got != want {
			t.Fatalf("actionSenderJID() = %v, want %v", got, want)
		}
	})

	t.Run("fromMe true with nil own JID falls back to chatJID", func(t *testing.T) {
		got := actionSenderJID(nil, chatJID, true)
		if got != chatJID {
			t.Fatalf("actionSenderJID() = %v, want %v", got, chatJID)
		}
	})

	t.Run("fromMe false falls back to chatJID", func(t *testing.T) {
		ownJID, _ := types.ParseJID("5562988888888:1@s.whatsapp.net")
		got := actionSenderJID(&ownJID, chatJID, false)
		if got != chatJID {
			t.Fatalf("actionSenderJID() = %v, want %v", got, chatJID)
		}
	})
}

// doHandlerRequest posts body (nil for none) to handler and returns the response.
// A nil *whatsmeow.Client is safe here: every case below is rejected before the
// handler dereferences client, so the 503 (client nil/disconnected) path isn't
// exercised by these tests — only the request-validation 405/400 paths are.
func doHandlerRequest(t *testing.T, handler http.HandlerFunc, method string, body []byte) *httptest.ResponseRecorder {
	t.Helper()
	var reqBody *bytes.Reader
	if body != nil {
		reqBody = bytes.NewReader(body)
	} else {
		reqBody = bytes.NewReader(nil)
	}
	req := httptest.NewRequest(method, "/", reqBody)
	rec := httptest.NewRecorder()
	handler(rec, req)
	return rec
}

// TestHandleReact covers /api/react request validation: method guard, decode
// guard, required-field guard, and the group + from_me=false rejection that
// prevents actionSenderJID from silently using the group JID as sender.
func TestHandleReact(t *testing.T) {
	handler := handleReact(nil)

	t.Run("non-POST returns 405", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodGet, nil)
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusMethodNotAllowed)
		}
	})

	t.Run("malformed JSON returns 400", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodPost, []byte("{not json"))
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("missing message_id returns 400", func(t *testing.T) {
		body, _ := json.Marshal(ReactRequest{ChatJID: "5562999999999@s.whatsapp.net", Emoji: "👍"})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("group chat with from_me=false returns 400", func(t *testing.T) {
		body, _ := json.Marshal(ReactRequest{ChatJID: "123456@g.us", MessageID: "MSG1", Emoji: "👍", FromMe: false})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
		var resp MarkChatResponse
		if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
			t.Fatalf("failed to decode response: %v", err)
		}
		if resp.Success {
			t.Fatalf("expected Success=false, got response: %+v", resp)
		}
	})
}

// TestHandleEdit covers /api/edit request validation. Edit has no group guard
// (WhatsApp only allows editing your own messages), so it isn't tested here.
func TestHandleEdit(t *testing.T) {
	handler := handleEdit(nil)

	t.Run("non-POST returns 405", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodGet, nil)
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusMethodNotAllowed)
		}
	})

	t.Run("malformed JSON returns 400", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodPost, []byte("{not json"))
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("missing chat_jid returns 400", func(t *testing.T) {
		body, _ := json.Marshal(EditRequest{MessageID: "MSG1", NewText: "hi"})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})
}

// TestHandleRevoke covers /api/revoke request validation: same shape as
// TestHandleReact, including the group + from_me=false rejection.
func TestHandleRevoke(t *testing.T) {
	handler := handleRevoke(nil)

	t.Run("non-POST returns 405", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodGet, nil)
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusMethodNotAllowed)
		}
	})

	t.Run("malformed JSON returns 400", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodPost, []byte("{not json"))
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("missing message_id returns 400", func(t *testing.T) {
		body, _ := json.Marshal(RevokeRequest{ChatJID: "5562999999999@s.whatsapp.net"})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("group chat with from_me=false returns 400", func(t *testing.T) {
		body, _ := json.Marshal(RevokeRequest{ChatJID: "123456@g.us", MessageID: "MSG1", FromMe: false})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
		var resp MarkChatResponse
		if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
			t.Fatalf("failed to decode response: %v", err)
		}
		if resp.Success {
			t.Fatalf("expected Success=false, got response: %+v", resp)
		}
	})
}

// TestHandleGroupParticipants covers /api/group_participants request
// validation: method guard, decode guard, group_jid @g.us guard, action
// whitelist, and the nil-client 503 path (which the action/JID checks in the
// handler must run before, so it's reached deterministically here).
func TestHandleGroupParticipants(t *testing.T) {
	handler := handleGroupParticipants(nil)

	t.Run("non-POST returns 405", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodGet, nil)
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusMethodNotAllowed)
		}
	})

	t.Run("malformed JSON returns 400", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodPost, []byte("{not json"))
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("missing participants returns 400", func(t *testing.T) {
		body, _ := json.Marshal(GroupParticipantsRequest{GroupJID: "123456@g.us", Action: "add"})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("group_jid not @g.us returns 400", func(t *testing.T) {
		body, _ := json.Marshal(GroupParticipantsRequest{GroupJID: "5562999999999@s.whatsapp.net", Participants: []string{"5562988887777"}, Action: "add"})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("invalid action returns 400", func(t *testing.T) {
		body, _ := json.Marshal(GroupParticipantsRequest{GroupJID: "123456@g.us", Participants: []string{"5562988887777"}, Action: "kick"})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("disconnected client returns 503", func(t *testing.T) {
		body, _ := json.Marshal(GroupParticipantsRequest{GroupJID: "123456@g.us", Participants: []string{"5562988887777"}, Action: "add"})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusServiceUnavailable {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusServiceUnavailable)
		}
	})
}

// TestHandleChatPresence covers /api/chat_presence request validation: method
// guard, decode guard, state whitelist, media whitelist, and 503 disconnected.
func TestHandleChatPresence(t *testing.T) {
	handler := handleChatPresence(nil)

	t.Run("non-POST returns 405", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodGet, nil)
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusMethodNotAllowed)
		}
	})

	t.Run("malformed JSON returns 400", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodPost, []byte("{not json"))
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("missing chat_jid returns 400", func(t *testing.T) {
		body, _ := json.Marshal(ChatPresenceRequest{State: "composing"})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("invalid state returns 400", func(t *testing.T) {
		body, _ := json.Marshal(ChatPresenceRequest{ChatJID: "5562999999999@s.whatsapp.net", State: "typing"})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("invalid media returns 400", func(t *testing.T) {
		body, _ := json.Marshal(ChatPresenceRequest{ChatJID: "5562999999999@s.whatsapp.net", State: "composing", Media: "video"})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("disconnected client returns 503", func(t *testing.T) {
		body, _ := json.Marshal(ChatPresenceRequest{ChatJID: "5562999999999@s.whatsapp.net", State: "composing"})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusServiceUnavailable {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusServiceUnavailable)
		}
	})
}

// TestHandleIsOnWhatsApp covers /api/is_on_whatsapp request validation: method
// guard, decode guard, empty phones guard, and 503 disconnected.
func TestHandleIsOnWhatsApp(t *testing.T) {
	handler := handleIsOnWhatsApp(nil)

	t.Run("non-POST returns 405", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodGet, nil)
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusMethodNotAllowed)
		}
	})

	t.Run("malformed JSON returns 400", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodPost, []byte("{not json"))
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("empty phones returns 400", func(t *testing.T) {
		body, _ := json.Marshal(IsOnWhatsAppRequest{Phones: []string{}})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("disconnected client returns 503", func(t *testing.T) {
		body, _ := json.Marshal(IsOnWhatsAppRequest{Phones: []string{"5562999999999"}})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusServiceUnavailable {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusServiceUnavailable)
		}
	})

	t.Run("malformed phone returns 400", func(t *testing.T) {
		body, _ := json.Marshal(IsOnWhatsAppRequest{Phones: []string{"+55 (62) 9999-9999"}})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("more than 50 phones returns 400", func(t *testing.T) {
		phones := make([]string, 51)
		for i := range phones {
			phones[i] = "5562999999999"
		}
		body, _ := json.Marshal(IsOnWhatsAppRequest{Phones: phones})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})
}

// TestParseGroupParticipantJIDs covers the pure parsing/validation logic
// behind /api/group_participants, independent of the whatsmeow client.
func TestParseGroupParticipantJIDs(t *testing.T) {
	t.Run("bare phone with internal space and hyphen normalizes", func(t *testing.T) {
		jids, err := parseGroupParticipantJIDs([]string{"55 62-99999-7777"})
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if jids[0].User != "5562999997777" || jids[0].Server != types.DefaultUserServer {
			t.Fatalf("got %+v", jids[0])
		}
	})

	t.Run("00 prefix is kept as literal digits, not stripped", func(t *testing.T) {
		jids, err := parseGroupParticipantJIDs([]string{"0055629999977"})
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if jids[0].User != "0055629999977" {
			t.Fatalf("got %q", jids[0].User)
		}
	})

	t.Run("full JID with default user server is accepted as-is", func(t *testing.T) {
		jids, err := parseGroupParticipantJIDs([]string{"5562999999999@s.whatsapp.net"})
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if jids[0].String() != "5562999999999@s.whatsapp.net" {
			t.Fatalf("got %q", jids[0].String())
		}
	})

	t.Run("empty item after trim returns error", func(t *testing.T) {
		if _, err := parseGroupParticipantJIDs([]string{"5562999999999", "  "}); err == nil {
			t.Fatal("expected error for empty participant")
		}
	})

	t.Run("123@lid is accepted as a participant", func(t *testing.T) {
		jids, err := parseGroupParticipantJIDs([]string{"123@lid"})
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if jids[0].Server != types.HiddenUserServer {
			t.Fatalf("got server %q", jids[0].Server)
		}
	})

	t.Run("unsupported server returns error", func(t *testing.T) {
		if _, err := parseGroupParticipantJIDs([]string{"123@foo.bar"}); err == nil {
			t.Fatal("expected error for unsupported server")
		}
	})

	t.Run("no participants after parsing returns error", func(t *testing.T) {
		if _, err := parseGroupParticipantJIDs([]string{}); err == nil {
			t.Fatal("expected error for empty list")
		}
	})
}

// TestNormalizeCheckPhones covers the pure validation/normalization logic
// behind /api/is_on_whatsapp, independent of the whatsmeow client.
func TestNormalizeCheckPhones(t *testing.T) {
	t.Run("internal space and hyphen normalize and gain +", func(t *testing.T) {
		phones, err := normalizeCheckPhones([]string{"55 62-99999-7777"})
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if phones[0] != "+5562999997777" {
			t.Fatalf("got %q", phones[0])
		}
	})

	t.Run("00 prefix rejected as invalid digit count edge case still normalizes", func(t *testing.T) {
		phones, err := normalizeCheckPhones([]string{"0055629999977"})
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if phones[0] != "+0055629999977" {
			t.Fatalf("got %q", phones[0])
		}
	})

	t.Run("plus already present is not duplicated", func(t *testing.T) {
		phones, err := normalizeCheckPhones([]string{"+5562999999999"})
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if phones[0] != "+5562999999999" {
			t.Fatalf("got %q", phones[0])
		}
	})

	t.Run("empty item returns error", func(t *testing.T) {
		if _, err := normalizeCheckPhones([]string{""}); err == nil {
			t.Fatal("expected error for empty phone")
		}
	})

	t.Run("non-digit characters return error", func(t *testing.T) {
		if _, err := normalizeCheckPhones([]string{"abc12345"}); err == nil {
			t.Fatal("expected error for non-digit phone")
		}
	})

	t.Run("more than 50 phones returns error", func(t *testing.T) {
		phones := make([]string, 51)
		for i := range phones {
			phones[i] = "5562999999999"
		}
		if _, err := normalizeCheckPhones(phones); err == nil {
			t.Fatal("expected error for exceeding cap")
		}
	})
}

// TestMergeIsOnWhatsAppResults covers the fill-in-omissions merge behind
// /api/is_on_whatsapp: whatsmeow omits unregistered numbers from its response
// instead of returning IsIn=false, so the merge must backfill them.
func TestMergeIsOnWhatsAppResults(t *testing.T) {
	t.Run("registered number keeps lib result", func(t *testing.T) {
		resp := []types.IsOnWhatsAppResponse{
			{Query: "+5562999999999", IsIn: true, JID: types.NewJID("5562999999999", types.DefaultUserServer)},
		}
		out := mergeIsOnWhatsAppResults([]string{"+5562999999999"}, resp)
		if len(out) != 1 || !out[0].IsIn || out[0].JID != "5562999999999@s.whatsapp.net" {
			t.Fatalf("got %+v", out)
		}
	})

	t.Run("unregistered number omitted by lib is backfilled as is_in false", func(t *testing.T) {
		resp := []types.IsOnWhatsAppResponse{
			{Query: "+556291788888", IsIn: true, JID: types.NewJID("556291788888", types.DefaultUserServer)},
		}
		out := mergeIsOnWhatsAppResults([]string{"+556291788888", "+5562000000000"}, resp)
		if len(out) != 2 {
			t.Fatalf("got %d results, want 2: %+v", len(out), out)
		}
		if out[0].Query != "+556291788888" || !out[0].IsIn {
			t.Fatalf("got[0] = %+v", out[0])
		}
		if out[1].Query != "+5562000000000" || out[1].IsIn || out[1].JID != "" {
			t.Fatalf("got[1] = %+v", out[1])
		}
	})

	t.Run("output order matches input order regardless of response order", func(t *testing.T) {
		resp := []types.IsOnWhatsAppResponse{
			{Query: "+5562000000002", IsIn: true, JID: types.NewJID("5562000000002", types.DefaultUserServer)},
		}
		out := mergeIsOnWhatsAppResults([]string{"+5562000000001", "+5562000000002", "+5562000000003"}, resp)
		if len(out) != 3 {
			t.Fatalf("got %d results, want 3", len(out))
		}
		wantOrder := []string{"+5562000000001", "+5562000000002", "+5562000000003"}
		for i, q := range wantOrder {
			if out[i].Query != q {
				t.Fatalf("out[%d].Query = %q, want %q", i, out[i].Query, q)
			}
		}
		if !out[1].IsIn {
			t.Fatalf("out[1] should be registered: %+v", out[1])
		}
	})
}

// T004 — Gap #12 tests

// TestHandleGroupInviteLink covers /api/group_invite_link request validation:
// method guard, decode guard, group_jid @g.us guard, and 503 disconnected.
func TestHandleGroupInviteLink(t *testing.T) {
	handler := handleGroupInviteLink(nil)

	t.Run("non-POST returns 405", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodGet, nil)
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusMethodNotAllowed)
		}
	})

	t.Run("malformed JSON returns 400", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodPost, []byte("{not json"))
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("missing group_jid returns 400", func(t *testing.T) {
		body, _ := json.Marshal(GroupInviteLinkRequest{Reset: false})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("group_jid not @g.us returns 400", func(t *testing.T) {
		body, _ := json.Marshal(GroupInviteLinkRequest{GroupJID: "5562999999999@s.whatsapp.net", Reset: false})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})
}

// TestHandleGroupInviteInfo covers /api/group_invite_info request validation.
func TestHandleGroupInviteInfo(t *testing.T) {
	handler := handleGroupInviteInfo(nil)

	t.Run("non-POST returns 405", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodGet, nil)
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusMethodNotAllowed)
		}
	})

	t.Run("malformed JSON returns 400", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodPost, []byte("{not json"))
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("missing link returns 400", func(t *testing.T) {
		body, _ := json.Marshal(GroupInviteInfoRequest{})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})
}

// TestHandleJoinGroup covers /api/join_group_with_link request validation.
func TestHandleJoinGroup(t *testing.T) {
	handler := handleJoinGroup(nil)

	t.Run("non-POST returns 405", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodGet, nil)
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusMethodNotAllowed)
		}
	})

	t.Run("malformed JSON returns 400", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodPost, []byte("{not json"))
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("missing link returns 400", func(t *testing.T) {
		body, _ := json.Marshal(JoinGroupRequest{})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})
}

// TestHandleGroupSettings covers /api/group_settings request validation.
func TestHandleGroupSettings(t *testing.T) {
	handler := handleGroupSettings(nil)

	t.Run("non-POST returns 405", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodGet, nil)
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusMethodNotAllowed)
		}
	})

	t.Run("malformed JSON returns 400", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodPost, []byte("{not json"))
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("missing group_jid returns 400", func(t *testing.T) {
		name := "test"
		body, _ := json.Marshal(GroupSettingsRequest{Name: &name})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("group_jid not @g.us returns 400", func(t *testing.T) {
		name := "test"
		body, _ := json.Marshal(GroupSettingsRequest{GroupJID: "5562999999999@s.whatsapp.net", Name: &name})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("no fields present returns 400", func(t *testing.T) {
		body, _ := json.Marshal(GroupSettingsRequest{GroupJID: "123456@g.us"})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})
}

// TestHandleGroupPhoto covers /api/group_photo request validation.
func TestHandleGroupPhoto(t *testing.T) {
	handler := handleGroupPhoto(nil)

	t.Run("non-POST returns 405", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodGet, nil)
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusMethodNotAllowed)
		}
	})

	t.Run("malformed JSON returns 400", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodPost, []byte("{not json"))
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("missing group_jid returns 400", func(t *testing.T) {
		body, _ := json.Marshal(GroupPhotoRequest{Remove: false, MediaPath: "a.jpg"})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("group_jid not @g.us returns 400", func(t *testing.T) {
		body, _ := json.Marshal(GroupPhotoRequest{GroupJID: "5562999999999@s.whatsapp.net", Remove: false, MediaPath: "a.jpg"})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("remove=true with media_path returns 400", func(t *testing.T) {
		body, _ := json.Marshal(GroupPhotoRequest{GroupJID: "123456@g.us", Remove: true, MediaPath: "a.jpg"})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("remove=false without media_path returns 400", func(t *testing.T) {
		body, _ := json.Marshal(GroupPhotoRequest{GroupJID: "123456@g.us", Remove: false, MediaPath: ""})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})
}

// TestHandleUserInfo covers /api/user_info request validation.
func TestHandleUserInfo(t *testing.T) {
	handler := handleUserInfo(nil)

	t.Run("non-POST returns 405", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodGet, nil)
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusMethodNotAllowed)
		}
	})

	t.Run("malformed JSON returns 400", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodPost, []byte("{not json"))
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("missing jids returns 400", func(t *testing.T) {
		body, _ := json.Marshal(UserInfoRequest{})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("too many jids returns 400", func(t *testing.T) {
		jids := make([]string, 21)
		for i := 0; i < 21; i++ {
			jids[i] = "5562999999999@s.whatsapp.net"
		}
		body, _ := json.Marshal(UserInfoRequest{JIDs: jids})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})
}

// TestHandleProfilePicture covers /api/profile_picture request validation.
func TestHandleProfilePicture(t *testing.T) {
	handler := handleProfilePicture(nil)

	t.Run("non-POST returns 405", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodGet, nil)
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusMethodNotAllowed)
		}
	})

	t.Run("malformed JSON returns 400", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodPost, []byte("{not json"))
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("missing jid returns 400", func(t *testing.T) {
		body, _ := json.Marshal(ProfilePictureRequest{})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("jid with invalid server returns 400", func(t *testing.T) {
		body, _ := json.Marshal(ProfilePictureRequest{JID: "5562999999999@invalid.server"})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})
}

// TestMergeUserInfoResults ensures output has one result per input query in order,
// with Found:false for omissions.
func TestMergeUserInfoResults(t *testing.T) {
	t.Run("output order matches input order", func(t *testing.T) {
		queries := []string{"5562000000001@s.whatsapp.net", "5562000000002@s.whatsapp.net", "5562000000003@s.whatsapp.net"}
		userInfoMap := make(map[types.JID]types.UserInfo)
		jid2, _ := types.ParseJID("5562000000002@s.whatsapp.net")
		userInfoMap[jid2] = types.UserInfo{Status: "Hello"}

		results := mergeUserInfoResults(queries, userInfoMap)
		if len(results) != 3 {
			t.Fatalf("got %d results, want 3", len(results))
		}
		if results[0].Query != queries[0] || results[0].Found {
			t.Fatalf("results[0] should be unfound for %q", queries[0])
		}
		if results[1].Query != queries[1] || !results[1].Found {
			t.Fatalf("results[1] should be found for %q", queries[1])
		}
		if results[1].Status != "Hello" {
			t.Fatalf("results[1].Status = %q, want 'Hello'", results[1].Status)
		}
		if results[2].Query != queries[2] || results[2].Found {
			t.Fatalf("results[2] should be unfound for %q", queries[2])
		}
	})

	// Regression: whatsmeow's usync calls jid.ToNonAD() before querying, so the
	// map it returns is keyed without device/agent. A caller passing an
	// AD-qualified JID (e.g. copied from a group message sender) must still be
	// correlated, instead of being reported as Found:false for a user WhatsApp
	// did resolve.
	t.Run("AD-qualified query matches non-AD map key", func(t *testing.T) {
		queries := []string{"5562000000001:26@s.whatsapp.net"}
		nonAD, _ := types.ParseJID("5562000000001@s.whatsapp.net")
		userInfoMap := map[types.JID]types.UserInfo{nonAD: {Status: "Hello"}}

		results := mergeUserInfoResults(queries, userInfoMap)
		if len(results) != 1 {
			t.Fatalf("got %d results, want 1", len(results))
		}
		if !results[0].Found {
			t.Fatalf("AD-qualified query %q was not correlated with its non-AD map key", queries[0])
		}
		if results[0].Status != "Hello" {
			t.Fatalf("results[0].Status = %q, want 'Hello'", results[0].Status)
		}
		if results[0].Query != queries[0] {
			t.Fatalf("results[0].Query = %q, want the caller's original %q", results[0].Query, queries[0])
		}
		if results[0].JID != nonAD.String() {
			t.Fatalf("results[0].JID = %q, want the resolved non-AD JID %q", results[0].JID, nonAD.String())
		}
	})
}

// TestHashPollOptions verifies that hash→name mapping is consistent and deterministic.
func TestHashPollOptions(t *testing.T) {
	t.Run("SHA256 hash consistency", func(t *testing.T) {
		options := []string{"Option A", "Option B"}

		// Compute hashes twice to verify determinism
		hashMap1 := make(map[string]string)
		hashMap2 := make(map[string]string)

		for _, opt := range options {
			hash := sha256.Sum256([]byte(opt))
			hashStr := hex.EncodeToString(hash[:])
			hashMap1[hashStr] = opt
			hashMap2[hashStr] = opt
		}

		if len(hashMap1) != len(options) {
			t.Fatalf("hashMap1 has %d entries, want %d", len(hashMap1), len(options))
		}
		if len(hashMap2) != len(options) {
			t.Fatalf("hashMap2 has %d entries, want %d", len(hashMap2), len(options))
		}

		// Verify hashes are identical on second computation
		for _, opt := range options {
			hash1 := sha256.Sum256([]byte(opt))
			hash2 := sha256.Sum256([]byte(opt))
			if hash1 != hash2 {
				t.Errorf("hash mismatch for %q", opt)
			}
		}
	})

	t.Run("unique options produce unique hashes", func(t *testing.T) {
		options := []string{"Yes", "No", "Maybe"}
		hashes := make(map[string]bool)

		for _, opt := range options {
			hash := sha256.Sum256([]byte(opt))
			hashStr := hex.EncodeToString(hash[:])
			if hashes[hashStr] {
				t.Errorf("hash collision detected for %q", opt)
			}
			hashes[hashStr] = true
		}
	})
}

// TestCreatePollEndpoint validates POST /api/create_poll behavior.
// The poll endpoint tests below replace an earlier version that built the
// request with httptest.NewRequest and then discarded it (`_ = req`), never
// calling the handler. Those passed with the validation code deleted, so RF-06
// had no coverage at all. These go through doHandlerRequest, which actually
// invokes the handler and returns the recorded response.
//
// A nil *whatsmeow.Client is fine here: every case is rejected during request
// validation, before the handler touches the client.

func TestHandleCreatePoll(t *testing.T) {
	handler := handleCreatePoll(nil, nil)
	valid := func() CreatePollRequest {
		return CreatePollRequest{
			ChatJID:         "120363000000000000@g.us",
			Question:        "smoke?",
			Options:         []string{"alpha", "beta"},
			SelectableCount: 1,
		}
	}

	t.Run("non-POST returns 405", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodGet, nil)
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("status = %d, want 405", rec.Code)
		}
	})

	t.Run("malformed JSON returns 400", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodPost, []byte("{not json"))
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want 400", rec.Code)
		}
	})

	rejected := []struct {
		name   string
		mutate func(*CreatePollRequest)
	}{
		{"empty chat_jid", func(r *CreatePollRequest) { r.ChatJID = "" }},
		{"blank question", func(r *CreatePollRequest) { r.Question = "   " }},
		{"single option", func(r *CreatePollRequest) { r.Options = []string{"alpha"} }},
		{"thirteen options", func(r *CreatePollRequest) {
			r.Options = make([]string, 13)
			for i := range r.Options {
				r.Options[i] = string(rune('a' + i))
			}
			r.SelectableCount = 1
		}},
		{"duplicate options", func(r *CreatePollRequest) { r.Options = []string{"alpha", "alpha"} }},
		// " alpha" and "alpha" are the same option once trimmed; accepting both
		// would produce two entries with the same SHA-256, making any vote for
		// them ambiguous by construction (RN-08).
		{"options differing only by surrounding space", func(r *CreatePollRequest) {
			r.Options = []string{"alpha", " alpha "}
		}},
		{"blank option", func(r *CreatePollRequest) { r.Options = []string{"alpha", "  "} }},
		// selectable_count 0 must not be forwarded: whatsmeow silently rewrites
		// an out-of-range value to 0, which means "no limit" — a different poll
		// from the one that was asked for (RN-07).
		{"selectable_count zero", func(r *CreatePollRequest) { r.SelectableCount = 0 }},
		{"selectable_count above option count", func(r *CreatePollRequest) { r.SelectableCount = 99 }},
		{"negative selectable_count", func(r *CreatePollRequest) { r.SelectableCount = -1 }},
	}
	for _, tc := range rejected {
		t.Run(tc.name+" returns 400", func(t *testing.T) {
			req := valid()
			tc.mutate(&req)
			body, _ := json.Marshal(req)
			rec := doHandlerRequest(t, handler, http.MethodPost, body)
			if rec.Code != http.StatusBadRequest {
				t.Fatalf("status = %d, want 400 (body: %s)", rec.Code, rec.Body.String())
			}
		})
	}

	// Guards the boundary from the other side: a request that satisfies every
	// rule must get past validation. Without this, a handler that returned 400
	// unconditionally would pass all the cases above.
	t.Run("valid request passes validation and reaches the client guard", func(t *testing.T) {
		body, _ := json.Marshal(valid())
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusServiceUnavailable {
			t.Fatalf("status = %d, want 503 (nil client), got body: %s", rec.Code, rec.Body.String())
		}
	})
}

func TestHandleVotePoll(t *testing.T) {
	handler := handleVotePoll(nil, nil)

	t.Run("non-POST returns 405", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodGet, nil)
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("status = %d, want 405", rec.Code)
		}
	})

	t.Run("malformed JSON returns 400", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodPost, []byte("{not json"))
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want 400", rec.Code)
		}
	})

	for _, tc := range []struct {
		name string
		req  VotePollRequest
	}{
		{"missing chat_jid", VotePollRequest{PollID: "MSG1", Options: []string{"alpha"}}},
		{"missing poll_id", VotePollRequest{ChatJID: "120363000000000000@g.us", Options: []string{"alpha"}}},
	} {
		t.Run(tc.name+" returns 400", func(t *testing.T) {
			body, _ := json.Marshal(tc.req)
			rec := doHandlerRequest(t, handler, http.MethodPost, body)
			if rec.Code != http.StatusBadRequest {
				t.Fatalf("status = %d, want 400 (body: %s)", rec.Code, rec.Body.String())
			}
		})
	}
}

// TestPollResultsTally drives handlePollResults against a real temp DB, which
// is the only way to cover the tally arithmetic — the validation-only tests
// below never reach it.
func TestPollResultsTally(t *testing.T) {
	store := setupPollStore(t)

	const pollID, chatJID = "POLL1", "120363000000000000@g.us"
	// polls has a foreign key to chats(jid), enforced on this connection —
	// same reason handleCreatePoll calls EnsureChat before StorePoll.
	if err := store.EnsureChat(chatJID, time.Now()); err != nil {
		t.Fatalf("EnsureChat: %v", err)
	}
	if err := store.StorePoll(pollID, chatJID, "me@s.whatsapp.net", "smoke?",
		`["alpha","beta","gama"]`, 1, time.Now().Unix()); err != nil {
		t.Fatalf("StorePoll: %v", err)
	}
	seed := []struct {
		voter    string
		selected string
		resolved int
	}{
		{"a@s.whatsapp.net", `["alpha"]`, 1},
		{"b@s.whatsapp.net", `["alpha"]`, 1},
		{"c@s.whatsapp.net", `[]`, 1}, // withdrew their vote
		{"d@s.whatsapp.net", `[]`, 0}, // poll options unknown to the bridge
	}
	for _, s := range seed {
		if err := store.UpsertPollVote(pollID, chatJID, s.voter, s.selected, s.resolved, time.Now().Unix()); err != nil {
			t.Fatalf("UpsertPollVote(%s): %v", s.voter, err)
		}
	}

	body, _ := json.Marshal(PollResultsRequest{ChatJID: chatJID, PollID: pollID})
	rec := doHandlerRequest(t, handlePollResults(store), http.MethodPost, body)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200 (body: %s)", rec.Code, rec.Body.String())
	}
	var resp PollResultsResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode: %v", err)
	}

	// Every option is reported, in the stored order, including the ones nobody
	// picked — omitting them would read as "that option did not exist".
	if len(resp.Results) != 3 {
		t.Fatalf("got %d options, want 3: %+v", len(resp.Results), resp.Results)
	}
	for i, want := range []string{"alpha", "beta", "gama"} {
		if resp.Results[i].Option != want {
			t.Errorf("Results[%d].Option = %q, want %q", i, resp.Results[i].Option, want)
		}
	}
	if resp.Results[0].Count != 2 {
		t.Errorf("alpha count = %d, want 2", resp.Results[0].Count)
	}
	if resp.Results[1].Count != 0 || resp.Results[2].Count != 0 {
		t.Errorf("unpicked options should be 0, got beta=%d gama=%d", resp.Results[1].Count, resp.Results[2].Count)
	}
	// The withdrawn vote is understood but is not a voter: total_voters must
	// never exceed the sum of the per-option counts.
	if resp.TotalVoters != 2 {
		t.Errorf("TotalVoters = %d, want 2 (withdrawn vote must not count)", resp.TotalVoters)
	}
	if resp.UnresolvedVotes != 1 {
		t.Errorf("UnresolvedVotes = %d, want 1", resp.UnresolvedVotes)
	}
}

func TestHandlePollResults(t *testing.T) {
	handler := handlePollResults(nil)

	t.Run("non-POST returns 405", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodGet, nil)
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("status = %d, want 405", rec.Code)
		}
	})

	t.Run("malformed JSON returns 400", func(t *testing.T) {
		rec := doHandlerRequest(t, handler, http.MethodPost, []byte("{not json"))
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want 400", rec.Code)
		}
	})

	t.Run("missing poll_id returns 400", func(t *testing.T) {
		body, _ := json.Marshal(PollResultsRequest{ChatJID: "120363000000000000@g.us"})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want 400 (body: %s)", rec.Code, rec.Body.String())
		}
	})
}

func TestPollVotesUpsertBehavior(t *testing.T) {
	t.Run("vote upsert overwrites on later timestamp", func(t *testing.T) {
		// Create temp DB
		store := setupPollStore(t)
		db := store.db

		pollID := "poll1"
		chatJID := "chat@s.whatsapp.net"
		voterJID := "voter@s.whatsapp.net"
		oldTime := time.Now().Unix() - 100
		newTime := time.Now().Unix()

		// First vote
		err := store.UpsertPollVote(pollID, chatJID, voterJID, `["A"]`, 1, oldTime)
		if err != nil {
			t.Fatalf("first upsert failed: %v", err)
		}

		// Second vote with later timestamp
		err = store.UpsertPollVote(pollID, chatJID, voterJID, `["B"]`, 1, newTime)
		if err != nil {
			t.Fatalf("second upsert failed: %v", err)
		}

		// Verify second vote is stored
		row := db.QueryRow(
			`SELECT selected FROM poll_votes WHERE poll_id=? AND chat_jid=? AND voter_jid=?`,
			pollID, chatJID, voterJID,
		)
		var selected string
		if err := row.Scan(&selected); err != nil {
			t.Fatalf("scan failed: %v", err)
		}
		if selected != `["B"]` {
			t.Errorf("expected ['B'], got %q", selected)
		}
	})

	t.Run("vote upsert ignores older timestamp", func(t *testing.T) {
		store := setupPollStore(t)
		db := store.db

		pollID := "poll1"
		chatJID := "chat@s.whatsapp.net"
		voterJID := "voter@s.whatsapp.net"
		newTime := time.Now().Unix()
		oldTime := newTime - 100

		// First vote with new timestamp
		err := store.UpsertPollVote(pollID, chatJID, voterJID, `["A"]`, 1, newTime)
		if err != nil {
			t.Fatalf("first upsert failed: %v", err)
		}

		// Second vote with old timestamp
		err = store.UpsertPollVote(pollID, chatJID, voterJID, `["B"]`, 1, oldTime)
		if err != nil {
			t.Fatalf("second upsert failed: %v", err)
		}

		// Verify first vote is still stored (not overwritten)
		row := db.QueryRow(
			`SELECT selected FROM poll_votes WHERE poll_id=? AND chat_jid=? AND voter_jid=?`,
			pollID, chatJID, voterJID,
		)
		var selected string
		if err := row.Scan(&selected); err != nil {
			t.Fatalf("scan failed: %v", err)
		}
		if selected != `["A"]` {
			t.Errorf("expected ['A'], got %q", selected)
		}
	})

	t.Run("unresolved vote stored even when poll unknown", func(t *testing.T) {
		store := setupPollStore(t)
		db := store.db

		pollID := "unknown_poll"
		chatJID := "chat@s.whatsapp.net"
		voterJID := "voter@s.whatsapp.net"

		// Upsert unresolved vote (poll doesn't exist in DB)
		err := store.UpsertPollVote(pollID, chatJID, voterJID, `[]`, 0, time.Now().Unix())
		if err != nil {
			t.Fatalf("upsert unresolved failed: %v", err)
		}

		// Verify vote is stored with resolved=0
		row := db.QueryRow(
			`SELECT resolved FROM poll_votes WHERE poll_id=? AND chat_jid=? AND voter_jid=?`,
			pollID, chatJID, voterJID,
		)
		var resolved int
		if err := row.Scan(&resolved); err != nil {
			t.Fatalf("scan failed: %v", err)
		}
		if resolved != 0 {
			t.Errorf("expected resolved=0, got %d", resolved)
		}
	})

	t.Run("multiple voters stored correctly", func(t *testing.T) {
		store := setupPollStore(t)
		db := store.db

		pollID := "poll1"
		chatJID := "chat@s.whatsapp.net"

		voters := []string{"voter1@s.whatsapp.net", "voter2@s.whatsapp.net"}
		for i, voterJID := range voters {
			err := store.UpsertPollVote(pollID, chatJID, voterJID, `["A"]`, 1, time.Now().Unix()-int64(i))
			if err != nil {
				t.Fatalf("upsert for voter %d failed: %v", i, err)
			}
		}

		// Count votes
		row := db.QueryRow(
			`SELECT COUNT(*) FROM poll_votes WHERE poll_id=? AND chat_jid=?`,
			pollID, chatJID,
		)
		var count int
		if err := row.Scan(&count); err != nil {
			t.Fatalf("scan failed: %v", err)
		}
		if count != 2 {
			t.Errorf("expected 2 votes, got %d", count)
		}
	})
}

// TestPollOptionResolution verifies hash→name resolution in vote processing.
// TestResolvePollVote exercises the real hash->name mapping. The previous
// version of this test rebuilt the mapping inline and asserted against its own
// arithmetic, so it passed no matter what the production code did; it is
// replaced here by a table that calls resolvePollVote itself.
func TestResolvePollVote(t *testing.T) {
	hashOf := func(s string) []byte {
		h := sha256.Sum256([]byte(s))
		return h[:]
	}
	options := `["Yes","No","Maybe"]`

	cases := []struct {
		name         string
		optionsJSON  string
		selected     [][]byte
		wantSelected string
		wantResolved int
	}{
		{
			name:         "every hash maps to a name",
			optionsJSON:  options,
			selected:     [][]byte{hashOf("Yes")},
			wantSelected: `["Yes"]`,
			wantResolved: 1,
		},
		{
			name:         "multiple selections keep vote order",
			optionsJSON:  options,
			selected:     [][]byte{hashOf("Maybe"), hashOf("Yes")},
			wantSelected: `["Maybe","Yes"]`,
			wantResolved: 1,
		},
		{
			name:         "withdrawn vote is resolved, not unknown",
			optionsJSON:  options,
			selected:     nil,
			wantSelected: `[]`,
			wantResolved: 1,
		},
		{
			name:         "hash matching no option marks the vote unresolved",
			optionsJSON:  options,
			selected:     [][]byte{hashOf("Yes"), hashOf("Something else")},
			wantSelected: `["Yes"]`,
			wantResolved: 0,
		},
		{
			name:         "unknown poll keeps the vote but cannot name it",
			optionsJSON:  "",
			selected:     [][]byte{hashOf("Yes")},
			wantSelected: `[]`,
			wantResolved: 0,
		},
		// Empty selection on purpose: with a non-empty selection this case would
		// pass even without the explicit unmarshal guard, because no hash would
		// match and the partial-resolution branch would already return 0. An
		// empty selection is the only input that separates "withdrawn vote we
		// understood" from "we never parsed the options", so it is the one that
		// actually covers the guard.
		{
			name:         "corrupt stored options are unresolved even with nothing selected",
			optionsJSON:  `{not json`,
			selected:     nil,
			wantSelected: `[]`,
			wantResolved: 0,
		},
		{
			name:         "corrupt stored options with a selection are unresolved too",
			optionsJSON:  `{not json`,
			selected:     [][]byte{hashOf("Yes")},
			wantSelected: `[]`,
			wantResolved: 0,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			gotSelected, gotResolved := resolvePollVote(tc.optionsJSON, tc.selected)
			if gotSelected != tc.wantSelected {
				t.Errorf("selected = %s, want %s", gotSelected, tc.wantSelected)
			}
			if gotResolved != tc.wantResolved {
				t.Errorf("resolved = %d, want %d", gotResolved, tc.wantResolved)
			}
		})
	}
}

// TestEvaluateHealth verifies the decision table for health evaluation (T001).
// Healthy is only true when connected AND logged in; reason varies based on why not.
func TestEvaluateHealth(t *testing.T) {
	cases := []struct {
		connected   bool
		loggedIn    bool
		wantHealthy bool
		wantReason  string
	}{
		{true, true, true, ""},
		{false, true, false, "disconnected from WhatsApp"},
		{true, false, false, "not logged in — scan the QR code at /qr"},
		{false, false, false, "not logged in — scan the QR code at /qr"},
	}

	for _, tc := range cases {
		t.Run(fmt.Sprintf("connected=%v,loggedIn=%v", tc.connected, tc.loggedIn), func(t *testing.T) {
			healthy, reason := evaluateHealth(tc.connected, tc.loggedIn)
			if healthy != tc.wantHealthy {
				t.Errorf("healthy = %v, want %v", healthy, tc.wantHealthy)
			}
			if reason != tc.wantReason {
				t.Errorf("reason = %q, want %q", reason, tc.wantReason)
			}
		})
	}
}

// TestDecideWatchdogAction verifies the watchdog decision table (T001).
func TestDecideWatchdogAction(t *testing.T) {
	cases := []struct {
		connected    bool
		loggedIn     bool
		wantDecision watchdogDecision
	}{
		{true, true, watchdogNone},
		{true, false, watchdogLoggedOut},
		{false, false, watchdogLoggedOut},
		{false, true, watchdogReconnect},
	}

	for _, tc := range cases {
		t.Run(fmt.Sprintf("connected=%v,loggedIn=%v", tc.connected, tc.loggedIn), func(t *testing.T) {
			decision := decideWatchdogAction(tc.connected, tc.loggedIn)
			if decision != tc.wantDecision {
				t.Errorf("decision = %q, want %q", decision, tc.wantDecision)
			}
		})
	}
}

// TestStatusEndpointWithNilClient verifies that GET /api/status returns 200
// (not 503) even when client == nil, with healthy=false and a non-empty reason (T003).
// These replace a first version that re-implemented the handler body inside the
// test ("mimics the actual handler") and asserted against its own copy. Proven
// dead by mutation: making the real handler answer 503 left them green. They now
// call handleStatus, which is why that handler had to stop being an inline
// closure.
// TestWatchdogStateConcurrency exists to give `go test -race` something to
// find. The watchdog goroutine and the event callback both write state that the
// status handler reads, but no other test ever starts them — so a green -race
// run was proving nothing about exactly the code that needed proving. It caught
// a real unsynchronized read once already: the loop logged the reconnect attempt
// number by reading watchdogState.reconnects after releasing the mutex.
//
// Note: -race needs CGO, so this is only meaningful on the CGO builds
// (Linux/macOS in CI, or WSL locally). On Windows it still runs, just without
// the detector.
func TestWatchdogStateConcurrency(t *testing.T) {
	const goroutines = 8
	const iterations = 200

	handler := handleStatus(nil)
	var wg sync.WaitGroup

	// Writers: the watchdog ticking through every decision.
	for i := 0; i < goroutines; i++ {
		wg.Add(1)
		go func(seed int) {
			defer wg.Done()
			decisions := []watchdogDecision{watchdogNone, watchdogReconnect, watchdogLoggedOut}
			for n := 0; n < iterations; n++ {
				applyWatchdogDecision(decisions[(seed+n)%len(decisions)])
			}
		}(i)
	}

	// Writer: the whatsmeow event callback stamping the last-event time.
	wg.Add(1)
	go func() {
		defer wg.Done()
		for n := 0; n < iterations*goroutines; n++ {
			lastEventAtNanos.Store(time.Now().UnixNano())
		}
	}()

	// Readers: concurrent health checks, which is what a monitor actually does.
	for i := 0; i < goroutines; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for n := 0; n < iterations; n++ {
				req := httptest.NewRequest(http.MethodGet, "/api/status", nil)
				rec := httptest.NewRecorder()
				handler(rec, req)
				if rec.Code != http.StatusOK {
					t.Errorf("status = %d during concurrent access, want 200", rec.Code)
					return
				}
			}
		}()
	}

	wg.Wait()
}

// TestApplyWatchdogDecision covers the state transitions the loop depends on,
// which used to be buried inside the ticker and therefore untestable.
func TestApplyWatchdogDecision(t *testing.T) {
	reset := func() {
		watchdogState.Lock()
		watchdogState.disconnectedTicks = 0
		watchdogState.loggedOutWarnTick = 0
		watchdogState.reconnects = 0
		watchdogState.Unlock()
	}

	t.Run("one disconnected tick is not enough to reconnect", func(t *testing.T) {
		reset()
		if reconnect, _, _ := applyWatchdogDecision(watchdogReconnect); reconnect {
			t.Fatal("reconnected on the first bad tick; the library's own backoff gets one tick first")
		}
	})

	t.Run("two consecutive disconnected ticks reconnect once", func(t *testing.T) {
		reset()
		applyWatchdogDecision(watchdogReconnect)
		reconnect, attempt, _ := applyWatchdogDecision(watchdogReconnect)
		if !reconnect {
			t.Fatal("did not reconnect after two consecutive bad ticks")
		}
		if attempt != 1 {
			t.Errorf("attempt = %d, want 1", attempt)
		}
		// The streak resets, so the next reconnect again needs two ticks.
		if again, _, _ := applyWatchdogDecision(watchdogReconnect); again {
			t.Error("reconnected again on the very next tick; the streak did not reset")
		}
	})

	t.Run("a healthy tick clears the streak", func(t *testing.T) {
		reset()
		applyWatchdogDecision(watchdogReconnect)
		applyWatchdogDecision(watchdogNone)
		if reconnect, _, _ := applyWatchdogDecision(watchdogReconnect); reconnect {
			t.Fatal("a single bad tick after recovery triggered a reconnect")
		}
	})

	t.Run("logged out warns on the first tick, not the tenth", func(t *testing.T) {
		reset()
		_, _, warn := applyWatchdogDecision(watchdogLoggedOut)
		if !warn {
			t.Fatal("no warning on the first logged-out tick — a dead session would stay silent for ten ticks")
		}
		for i := 0; i < 8; i++ {
			if _, _, w := applyWatchdogDecision(watchdogLoggedOut); w {
				t.Fatalf("warned again at tick %d; it should be rate-limited", i+2)
			}
		}
	})

	t.Run("logged out never asks for a reconnect", func(t *testing.T) {
		reset()
		for i := 0; i < 25; i++ {
			if reconnect, _, _ := applyWatchdogDecision(watchdogLoggedOut); reconnect {
				t.Fatal("asked to reconnect while logged out; only a QR scan fixes that")
			}
		}
	})
}

func TestHandleStatus(t *testing.T) {
	handler := handleStatus(nil)

	t.Run("non-GET returns 405", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodPost, "/api/status", nil)
		rec := httptest.NewRecorder()
		handler(rec, req)
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("status = %d, want 405", rec.Code)
		}
	})

	// The load-bearing case: a bridge that is up but not usable must still
	// answer 200. Returning 503 here would make "process dead" (connection
	// refused) indistinguishable from "process alive, session logged out".
	t.Run("nil client answers 200 with healthy=false and a reason", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/api/status", nil)
		rec := httptest.NewRecorder()
		handler(rec, req)

		if rec.Code != http.StatusOK {
			t.Fatalf("status = %d, want 200 — an unusable bridge must still answer 200 (body: %s)",
				rec.Code, rec.Body.String())
		}
		var resp StatusResponse
		if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
			t.Fatalf("decode: %v (body: %s)", err, rec.Body.String())
		}
		if !resp.Success {
			t.Error("Success = false; it means 'I answered', not 'all is well', so it should be true")
		}
		if resp.Healthy {
			t.Error("Healthy = true with no client")
		}
		if resp.Reason == "" {
			t.Error("Reason is empty — 'not healthy' without a reason does not say what to do")
		}
		if resp.Connected || resp.LoggedIn {
			t.Errorf("Connected=%v LoggedIn=%v, want both false", resp.Connected, resp.LoggedIn)
		}
		// Zero timestamps must be omitted rather than serialized as year 1.
		if resp.LastSuccessfulConnect != "" {
			t.Errorf("LastSuccessfulConnect = %q, want omitted while zero", resp.LastSuccessfulConnect)
		}
	})

	t.Run("content type is JSON", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/api/status", nil)
		rec := httptest.NewRecorder()
		handler(rec, req)
		if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
			t.Errorf("Content-Type = %q, want application/json", ct)
		}
	})
}

// setupPollStore gives a test a real MessageStore on a throwaway database.
//
// It goes through NewMessageStore instead of sql.Open with a literal driver
// name: the project registers a different driver per platform (mattn/go-sqlite3
// under CGO, modernc on Windows), so a hardcoded "sqlite" compiles everywhere
// and fails at run time on macOS and Linux — which is exactly how CI caught the
// first version of this helper.
//
// Using the production opener also means the schema under test is the real
// CREATE TABLE block, not a copy maintained by hand next to it. A column added
// to polls or poll_votes reaches these tests automatically instead of drifting.
func setupPollStore(t *testing.T) *MessageStore {
	t.Helper()
	orig, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	// Cleanups run LIFO, so registering the chdir-back after t.TempDir makes it
	// run before TempDir removal — Windows cannot delete a process's CWD.
	t.Cleanup(func() { _ = os.Chdir(orig) })
	if err := os.Chdir(dir); err != nil {
		t.Fatal(err)
	}
	store, err := NewMessageStore()
	if err != nil {
		t.Fatalf("NewMessageStore: %v", err)
	}
	t.Cleanup(func() { _ = store.Close() })
	return store
}
