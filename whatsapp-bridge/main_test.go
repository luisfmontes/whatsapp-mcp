package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"
	"unicode/utf8"

	waProto "go.mau.fi/whatsmeow/binary/proto"
	"go.mau.fi/whatsmeow/types"
	"google.golang.org/protobuf/proto"
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
		got := actionSenderJID(&ownJID, chatJID, types.JID{}, true)
		want := ownJID.ToNonAD()
		if got != want {
			t.Fatalf("actionSenderJID() = %v, want %v", got, want)
		}
	})

	t.Run("fromMe true with nil own JID falls back to chatJID", func(t *testing.T) {
		got := actionSenderJID(nil, chatJID, types.JID{}, true)
		if got != chatJID {
			t.Fatalf("actionSenderJID() = %v, want %v", got, chatJID)
		}
	})

	t.Run("fromMe false with no participant falls back to chatJID", func(t *testing.T) {
		ownJID, _ := types.ParseJID("5562988888888:1@s.whatsapp.net")
		got := actionSenderJID(&ownJID, chatJID, types.JID{}, false)
		if got != chatJID {
			t.Fatalf("actionSenderJID() = %v, want %v", got, chatJID)
		}
	})
}

// TestActionSenderJIDGrupo covers task 6/D2: reacting or revoking a third
// party's message in a group must use the AUTHOR's JID as sender, never the
// group's own JID — actionSenderJID(fromMe=false, participantJID=<author>) is
// exactly what handleReact/handleRevoke feed into BuildReaction/BuildRevoke
// as the sender argument. Identifiers below are deliberately not
// phone-shaped, same convention as TestSendQuotedRecusa.
func TestActionSenderJIDGrupo(t *testing.T) {
	groupJID, err := types.ParseJID("grupo-teste@g.us")
	if err != nil {
		t.Fatalf("unexpected error parsing group JID: %v", err)
	}

	t.Run("terceiro_em_grupo_usa_participante", func(t *testing.T) {
		participantJID, err := types.ParseJID("participante-autor@s.whatsapp.net")
		if err != nil {
			t.Fatalf("unexpected error parsing participant JID: %v", err)
		}
		got := actionSenderJID(nil, groupJID, participantJID, false)
		if got != participantJID {
			t.Fatalf("actionSenderJID() = %v, want the participant JID %v (not the group JID)", got, participantJID)
		}
		if got == groupJID {
			t.Fatalf("actionSenderJID() returned the group JID — third-party react/revoke would use the wrong sender")
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
// guard, required-field guard, and the group + from_me=false path (task 6):
// an unknown-author message (never stored, D9) still refuses; a real store
// with no matching row hits the exact same sql.ErrNoRows path.
func TestHandleReact(t *testing.T) {
	handler := handleReact(nil, nil)

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
		body, _ := json.Marshal(ReactRequest{ChatJID: "grupo-teste@g.us", Emoji: "👍"})
		rec := doHandlerRequest(t, handler, http.MethodPost, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
		}
	})

	t.Run("group chat, from_me=false, unknown author still returns 400", func(t *testing.T) {
		store := setupPollStore(t)
		handlerWithStore := handleReact(nil, store)
		body, _ := json.Marshal(ReactRequest{ChatJID: "grupo-teste@g.us", MessageID: "MSG1", Emoji: "👍", FromMe: false})
		rec := doHandlerRequest(t, handlerWithStore, http.MethodPost, body)
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
		if !strings.Contains(resp.Message, "unknown") {
			t.Errorf("message = %q, want it to say the author is unknown", resp.Message)
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
// TestHandleReact, including the group + from_me=false path (task 6).
func TestHandleRevoke(t *testing.T) {
	handler := handleRevoke(nil, nil)

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

	t.Run("group chat, from_me=false, unknown author still returns 400", func(t *testing.T) {
		store := setupPollStore(t)
		handlerWithStore := handleRevoke(nil, store)
		body, _ := json.Marshal(RevokeRequest{ChatJID: "grupo-teste@g.us", MessageID: "MSG1", FromMe: false})
		rec := doHandlerRequest(t, handlerWithStore, http.MethodPost, body)
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
		if !strings.Contains(resp.Message, "unknown") {
			t.Errorf("message = %q, want it to say the author is unknown", resp.Message)
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
		jids, err := parseGroupParticipantJIDs([]string{"55 62-99999-7777"}, "g@g.us")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if jids[0].User != "5562999997777" || jids[0].Server != types.DefaultUserServer {
			t.Fatalf("got %+v", jids[0])
		}
	})

	t.Run("00 prefix is kept as literal digits, not stripped", func(t *testing.T) {
		jids, err := parseGroupParticipantJIDs([]string{"0055629999977"}, "g@g.us")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if jids[0].User != "0055629999977" {
			t.Fatalf("got %q", jids[0].User)
		}
	})

	t.Run("full JID with default user server is accepted as-is", func(t *testing.T) {
		jids, err := parseGroupParticipantJIDs([]string{"5562999999999@s.whatsapp.net"}, "g@g.us")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if jids[0].String() != "5562999999999@s.whatsapp.net" {
			t.Fatalf("got %q", jids[0].String())
		}
	})

	t.Run("empty item after trim returns error", func(t *testing.T) {
		if _, err := parseGroupParticipantJIDs([]string{"5562999999999", "  "}, "g@g.us"); err == nil {
			t.Fatal("expected error for empty participant")
		}
	})

	t.Run("123@lid is accepted as a participant", func(t *testing.T) {
		jids, err := parseGroupParticipantJIDs([]string{"123@lid"}, "g@g.us")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if jids[0].Server != types.HiddenUserServer {
			t.Fatalf("got server %q", jids[0].Server)
		}
	})

	t.Run("unsupported server returns error", func(t *testing.T) {
		if _, err := parseGroupParticipantJIDs([]string{"123@foo.bar"}, "g@g.us"); err == nil {
			t.Fatal("expected error for unsupported server")
		}
	})

	t.Run("no participants after parsing returns error", func(t *testing.T) {
		if _, err := parseGroupParticipantJIDs([]string{}, "g@g.us"); err == nil {
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
			ChatJID:         "grupo-teste@g.us",
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
		{"missing poll_id", VotePollRequest{ChatJID: "grupo-teste@g.us", Options: []string{"alpha"}}},
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

	const pollID, chatJID = "POLL1", "grupo-teste@g.us"
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
		body, _ := json.Marshal(PollResultsRequest{ChatJID: "grupo-teste@g.us"})
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
// TestAccountScopedTempPaths validates the behavior of accountScopedTempFilename:
// without WHATSAPP_ACCOUNT, filenames are unchanged; with it set, the account
// name is inserted before the extension; invalid characters in the account name
// are rejected.
func TestAccountScopedTempPaths(t *testing.T) {
	// Save original env to restore after test
	orig := os.Getenv("WHATSAPP_ACCOUNT")
	t.Cleanup(func() { os.Setenv("WHATSAPP_ACCOUNT", orig) })

	t.Run("without WHATSAPP_ACCOUNT returns basename unchanged", func(t *testing.T) {
		os.Unsetenv("WHATSAPP_ACCOUNT")
		got, err := accountScopedTempFilename("whatsapp-qr.png")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if got != "whatsapp-qr.png" {
			t.Errorf("got %q, want %q", got, "whatsapp-qr.png")
		}
	})

	t.Run("with WHATSAPP_ACCOUNT=trabalho inserts suffix before extension", func(t *testing.T) {
		os.Setenv("WHATSAPP_ACCOUNT", "trabalho")
		got, err := accountScopedTempFilename("whatsapp-qr.png")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if got != "whatsapp-qr-trabalho.png" {
			t.Errorf("got %q, want %q", got, "whatsapp-qr-trabalho.png")
		}
	})

	t.Run("with WHATSAPP_ACCOUNT=pessoal inserts suffix before extension", func(t *testing.T) {
		os.Setenv("WHATSAPP_ACCOUNT", "pessoal")
		got, err := accountScopedTempFilename("wa_transcribe.lock")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if got != "wa_transcribe-pessoal.lock" {
			t.Errorf("got %q, want %q", got, "wa_transcribe-pessoal.lock")
		}
	})

	t.Run("forward slash in account name is rejected", func(t *testing.T) {
		os.Setenv("WHATSAPP_ACCOUNT", "bad/name")
		_, err := accountScopedTempFilename("whatsapp-qr.png")
		if err == nil {
			t.Fatal("expected error for forward slash")
		}
	})

	t.Run("backslash in account name is rejected", func(t *testing.T) {
		os.Setenv("WHATSAPP_ACCOUNT", "bad\\name")
		_, err := accountScopedTempFilename("whatsapp-qr.png")
		if err == nil {
			t.Fatal("expected error for backslash")
		}
	})

	t.Run("colon in account name is rejected", func(t *testing.T) {
		os.Setenv("WHATSAPP_ACCOUNT", "bad:name")
		_, err := accountScopedTempFilename("whatsapp-qr.png")
		if err == nil {
			t.Fatal("expected error for colon")
		}
	})

	t.Run("double dot (..) in account name is rejected", func(t *testing.T) {
		os.Setenv("WHATSAPP_ACCOUNT", "bad..name")
		_, err := accountScopedTempFilename("whatsapp-qr.png")
		if err == nil {
			t.Fatal("expected error for double dot")
		}
	})

	t.Run("file without extension uses empty string as extension", func(t *testing.T) {
		os.Setenv("WHATSAPP_ACCOUNT", "trabalho")
		got, err := accountScopedTempFilename("lockfile")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if got != "lockfile-trabalho" {
			t.Errorf("got %q, want %q", got, "lockfile-trabalho")
		}
	})

	t.Run("multiple dots are handled correctly", func(t *testing.T) {
		os.Setenv("WHATSAPP_ACCOUNT", "trabalho")
		got, err := accountScopedTempFilename("archive.tar.gz")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		// filepath.Ext only returns the last extension
		if got != "archive.tar-trabalho.gz" {
			t.Errorf("got %q, want %q", got, "archive.tar-trabalho.gz")
		}
	})
}

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

// TestQRPageIdentifiesAccount verifies renderQRPage() function behavior (extracted for testability).
// When accountAlias is set, includes account identification (D8, D3).
// When accountAlias is empty, page is unchanged from before (D1) — no duplication.
func TestQRPageIdentifiesAccount(t *testing.T) {
	port := 3090

	t.Run("renderQRPage with account=trabalho includes account and port", func(t *testing.T) {
		// Call the REAL function (not reimplemented)
		html := renderQRPage("trabalho", port)

		// Verify account identification is present
		if !strings.Contains(html, "Account: trabalho (port 3090)") {
			t.Errorf("HTML should contain 'Account: trabalho (port 3090)', got: %q", html)
		}
		if !strings.Contains(html, "<p>Account: trabalho (port 3090)</p>") {
			t.Errorf("HTML should have account in <p> tag, got: %q", html)
		}
	})

	t.Run("renderQRPage with empty account has no duplication (D1)", func(t *testing.T) {
		// Call the REAL function with empty account
		html := renderQRPage("", port)

		// Verify heading appears exactly once (in <h2>)
		count := strings.Count(html, "Scan with WhatsApp to connect")
		if count != 1 {
			t.Errorf("heading 'Scan with WhatsApp to connect' should appear exactly 1 time, got %d times", count)
		}

		// Verify no Account: line exists
		if strings.Contains(html, "Account:") {
			t.Errorf("HTML should not contain 'Account:' when no account specified, got: %q", html)
		}
	})

	t.Run("renderQRPage with account preserves format", func(t *testing.T) {
		// Call the REAL function
		html := renderQRPage("pessoal", 3091)

		// Verify format is correct
		if !strings.Contains(html, "<!DOCTYPE html>") {
			t.Error("HTML should have DOCTYPE")
		}
		if !strings.Contains(html, "<h2>Scan with WhatsApp to connect</h2>") {
			t.Error("HTML should have correct h2 heading")
		}
		if !strings.Contains(html, "Account: pessoal (port 3091)") {
			t.Error("HTML should have correct account info")
		}
	})
}

// setupChatStore returns a MessageStore backed by the production schema in a
// fresh temp dir, following the same os.Chdir(t.TempDir()) pattern as
// setupPollStore above — so listChats/getChat run against the real CREATE
// TABLE block instead of a hand-maintained copy, and the test needs no
// machine-local database.
func setupChatStore(t *testing.T) *MessageStore {
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

func boolPtr(b bool) *bool {
	return &b
}

// seedChatWithLastMessage inserts one chat and one message that is that
// chat's last message: last_message_time matches messages.timestamp, which
// is what the LEFT JOIN in listChats/getChat keys on.
func seedChatWithLastMessage(t *testing.T, db *sql.DB, jid, name, content, sender string, isFromMe bool, ts time.Time) {
	t.Helper()
	if _, err := db.Exec(`INSERT INTO chats (jid, name, last_message_time) VALUES (?, ?, ?)`, jid, name, ts); err != nil {
		t.Fatalf("insert chat: %v", err)
	}
	if _, err := db.Exec(`INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me) VALUES (?, ?, ?, ?, ?, ?)`,
		"msg1", jid, sender, content, ts, isFromMe); err != nil {
		t.Fatalf("insert message: %v", err)
	}
}

// TestListChatsIncludeLastMessage is the regression test for the 500 that
// include_last_message:false triggered in /api/chats: the SELECT projected
// messages.content/sender/is_from_me unconditionally while the JOIN that
// makes those columns exist was conditional ("no such column:
// messages.content"). D1's fix keeps 6 columns in the projection always,
// swapping in NULL literals for the message columns when the JOIN is
// skipped, so scanAPIChatRow (shared by 4 callers) never changes.
func TestListChatsIncludeLastMessage(t *testing.T) {
	store := setupChatStore(t)
	ts := time.Date(2026, 8, 23, 10, 0, 0, 0, time.UTC)
	seedChatWithLastMessage(t, store.db, "123@s.whatsapp.net", "Alice", "oi", "123@s.whatsapp.net", false, ts)

	t.Run("false: no SQL error, three fields nil", func(t *testing.T) {
		resp, err := listChats(store.db, ChatsRequest{Limit: 10, IncludeLastMessage: boolPtr(false)})
		if err != nil {
			t.Fatalf("listChats: %v", err)
		}
		if len(resp.Chats) != 1 {
			t.Fatalf("got %d chats, want 1", len(resp.Chats))
		}
		chat := resp.Chats[0]
		if chat.LastMessage != nil || chat.LastSender != nil || chat.LastIsFromMe != nil {
			t.Errorf("expected nil last_message/last_sender/last_is_from_me, got %v / %v / %v",
				chat.LastMessage, chat.LastSender, chat.LastIsFromMe)
		}
	})

	t.Run("true: three fields filled", func(t *testing.T) {
		resp, err := listChats(store.db, ChatsRequest{Limit: 10, IncludeLastMessage: boolPtr(true)})
		if err != nil {
			t.Fatalf("listChats: %v", err)
		}
		if len(resp.Chats) != 1 {
			t.Fatalf("got %d chats, want 1", len(resp.Chats))
		}
		chat := resp.Chats[0]
		if chat.LastMessage == nil || *chat.LastMessage != "oi" {
			t.Errorf("last_message = %v, want %q", chat.LastMessage, "oi")
		}
		if chat.LastSender == nil || *chat.LastSender != "123@s.whatsapp.net" {
			t.Errorf("last_sender = %v, want sender jid", chat.LastSender)
		}
		if chat.LastIsFromMe == nil || *chat.LastIsFromMe != false {
			t.Errorf("last_is_from_me = %v, want false", chat.LastIsFromMe)
		}
	})
}

// TestGetChatIncludeLastMessage mirrors TestListChatsIncludeLastMessage for
// /api/chat: same defect ("no such column: m.content"), same fix.
func TestGetChatIncludeLastMessage(t *testing.T) {
	store := setupChatStore(t)
	ts := time.Date(2026, 8, 23, 11, 0, 0, 0, time.UTC)
	seedChatWithLastMessage(t, store.db, "456@s.whatsapp.net", "Bob", "tudo bem", "456@s.whatsapp.net", true, ts)

	t.Run("false: no SQL error, three fields nil", func(t *testing.T) {
		resp, err := getChat(store.db, ChatRequest{ChatJID: "456@s.whatsapp.net", IncludeLastMessage: boolPtr(false)})
		if err != nil {
			t.Fatalf("getChat: %v", err)
		}
		if resp.Chat == nil {
			t.Fatal("expected a chat, got nil")
		}
		if resp.Chat.LastMessage != nil || resp.Chat.LastSender != nil || resp.Chat.LastIsFromMe != nil {
			t.Errorf("expected nil last_message/last_sender/last_is_from_me, got %v / %v / %v",
				resp.Chat.LastMessage, resp.Chat.LastSender, resp.Chat.LastIsFromMe)
		}
	})

	t.Run("true: three fields filled", func(t *testing.T) {
		resp, err := getChat(store.db, ChatRequest{ChatJID: "456@s.whatsapp.net", IncludeLastMessage: boolPtr(true)})
		if err != nil {
			t.Fatalf("getChat: %v", err)
		}
		if resp.Chat == nil {
			t.Fatal("expected a chat, got nil")
		}
		if resp.Chat.LastMessage == nil || *resp.Chat.LastMessage != "tudo bem" {
			t.Errorf("last_message = %v, want %q", resp.Chat.LastMessage, "tudo bem")
		}
		if resp.Chat.LastSender == nil || *resp.Chat.LastSender != "456@s.whatsapp.net" {
			t.Errorf("last_sender = %v, want sender jid", resp.Chat.LastSender)
		}
		if resp.Chat.LastIsFromMe == nil || *resp.Chat.LastIsFromMe != true {
			t.Errorf("last_is_from_me = %v, want true", resp.Chat.LastIsFromMe)
		}
	})
}

// TestQRPagesNeverIdentifyTheSameAccount is the criterion the earlier rounds missed:
// not "does the page contain a string", but "can the PERSON tell the two pages apart".
// With two accounts configured, every state the bridge serves must differ between them
// -- otherwise someone holding a phone scans the wrong QR and pairs the wrong account.
func TestQRPagesNeverIdentifyTheSameAccount(t *testing.T) {
	states := map[string]func(string, int) string{
		"qr":        renderQRPage,
		"connected": renderConnectedPage,
		"waiting":   renderWaitingPage,
	}

	for name, render := range states {
		t.Run(name+" distinguishes two named accounts", func(t *testing.T) {
			a := render("pessoal", 3005)
			b := render("trabalho", 3006)
			if a == b {
				t.Fatalf("%s page is identical for two accounts", name)
			}
			if !strings.Contains(bodyOf(a), "pessoal") || !strings.Contains(bodyOf(b), "trabalho") {
				t.Errorf("%s body must name its account; got a=%q b=%q", name, a, b)
			}
		})

		t.Run(name+" distinguishes two unnamed bridges by port", func(t *testing.T) {
			// The body, not the whole page: the <title> alone would make the
			// pages differ while the visible page stayed anonymous.
			a := bodyOf(render("", 3005))
			b := bodyOf(render("", 3006))
			if a == b {
				t.Fatalf("%s body is identical for two ports with no alias set", name)
			}
			if !strings.Contains(a, "3005") || !strings.Contains(b, "3006") {
				t.Errorf("%s body must fall back to the port; got a=%q b=%q", name, a, b)
			}
		})

		t.Run(name+" puts the identity in the title too", func(t *testing.T) {
			page := render("trabalho", 3006)
			if !strings.Contains(page, "<title>WhatsApp - trabalho</title>") {
				t.Errorf("%s page should title the tab with the account, got: %q", name, page)
			}
		})
	}
}

// TestAccountHeadingEscapesTheAlias -- the alias comes from the environment, so it is
// input, not a constant.
func TestAccountHeadingEscapesTheAlias(t *testing.T) {
	out := accountHeading("<script>alert(1)</script>", 3005)
	if strings.Contains(out, "<script>") {
		t.Errorf("alias must be escaped, got: %q", out)
	}
	if !strings.Contains(out, "&lt;script&gt;") {
		t.Errorf("expected the escaped alias in the output, got: %q", out)
	}
}

// bodyOf returns what the person actually sees, dropping <head>: a page whose only
// distinguishing mark is the tab title still leaves the open page ambiguous.
func bodyOf(page string) string {
	if i := strings.Index(page, "</head>"); i >= 0 {
		return page[i:]
	}
	return page
}

// TestAccountHeadingAlwaysIdentifies pins the heading itself, so deleting it cannot
// hide behind the title.
func TestAccountHeadingAlwaysIdentifies(t *testing.T) {
	if h := accountHeading("trabalho", 3006); !strings.Contains(h, "trabalho") || !strings.Contains(h, "3006") {
		t.Errorf("named account heading should carry alias and port, got %q", h)
	}
	if h := accountHeading("", 3005); !strings.Contains(h, "3005") {
		t.Errorf("unnamed bridge heading should carry the port, got %q", h)
	}
}

// setupLegacyMessagesStore creates a temp SQLite database (via the real,
// platform-registered driver, same as openMessagesDB) with the messages table
// in the shape it had before D8/D15 — the 13 columns from the CREATE TABLE
// block, none of sender_jid/quoted_message_id/quoted_sender/quoted_content/
// mentions. This is what the real store looked like before this change, and
// what ensureMessagesSchema has to migrate.
func setupLegacyMessagesStore(t *testing.T) *sql.DB {
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
	if err := os.MkdirAll("store", 0755); err != nil {
		t.Fatal(err)
	}
	db, err := openMessagesDB()
	if err != nil {
		t.Fatalf("openMessagesDB: %v", err)
	}
	t.Cleanup(func() { _ = db.Close() })

	_, err = db.Exec(`
		CREATE TABLE messages (
			id TEXT,
			chat_jid TEXT,
			sender TEXT,
			content TEXT,
			timestamp TIMESTAMP,
			is_from_me BOOLEAN,
			media_type TEXT,
			filename TEXT,
			url TEXT,
			media_key BLOB,
			file_sha256 BLOB,
			file_enc_sha256 BLOB,
			file_length INTEGER,
			PRIMARY KEY (id, chat_jid)
		);
	`)
	if err != nil {
		t.Fatalf("failed to create legacy messages table: %v", err)
	}
	return db
}

// messagesColumnNames reads the current column names of the messages table
// via PRAGMA table_info, the same source ensureMessagesSchema reads from.
func messagesColumnNames(t *testing.T, db *sql.DB) []string {
	t.Helper()
	rows, err := db.Query("PRAGMA table_info(messages)")
	if err != nil {
		t.Fatalf("PRAGMA table_info(messages): %v", err)
	}
	defer rows.Close()

	var names []string
	for rows.Next() {
		var cid int
		var name, colType string
		var notNull int
		var dfltValue sql.NullString
		var pk int
		if err := rows.Scan(&cid, &name, &colType, &notNull, &dfltValue, &pk); err != nil {
			t.Fatalf("scan table_info row: %v", err)
		}
		names = append(names, name)
	}
	if err := rows.Err(); err != nil {
		t.Fatalf("iterate table_info rows: %v", err)
	}
	return names
}

// TestEnsureMessagesSchema covers D15: CREATE TABLE IF NOT EXISTS does not
// add a column to a messages table that already exists, and the real store
// (128,377 rows) predates sender_jid/quoted_*/mentions. ensureMessagesSchema
// is what migrates it, and has to do so without erroring when run again.
func TestEnsureMessagesSchema(t *testing.T) {
	newColumns := []string{"sender_jid", "quoted_message_id", "quoted_sender", "quoted_content", "mentions"}

	t.Run("banco_legado_ganha_as_cinco_colunas", func(t *testing.T) {
		db := setupLegacyMessagesStore(t)

		before := messagesColumnNames(t, db)
		beforeSet := make(map[string]bool, len(before))
		for _, name := range before {
			beforeSet[name] = true
		}
		for _, want := range newColumns {
			if beforeSet[want] {
				t.Fatalf("legacy fixture unexpectedly already has column %q; got %v", want, before)
			}
		}

		if err := ensureMessagesSchema(db); err != nil {
			t.Fatalf("ensureMessagesSchema: %v", err)
		}

		after := messagesColumnNames(t, db)
		afterSet := make(map[string]bool, len(after))
		for _, name := range after {
			afterSet[name] = true
		}
		for _, want := range newColumns {
			if !afterSet[want] {
				t.Errorf("column %q missing after ensureMessagesSchema; got %v", want, after)
			}
		}
	})

	t.Run("idempotente_segunda_chamada_nao_falha_nem_duplica", func(t *testing.T) {
		db := setupLegacyMessagesStore(t)

		if err := ensureMessagesSchema(db); err != nil {
			t.Fatalf("ensureMessagesSchema (1st run): %v", err)
		}
		if err := ensureMessagesSchema(db); err != nil {
			t.Fatalf("ensureMessagesSchema (2nd run): %v", err)
		}

		after := messagesColumnNames(t, db)
		counts := make(map[string]int, len(after))
		for _, name := range after {
			counts[name]++
		}
		for _, col := range newColumns {
			if counts[col] != 1 {
				t.Errorf("column %q appears %d times after two runs, want exactly 1; got %v", col, counts[col], after)
			}
		}
	})

	t.Run("banco_novo_via_NewMessageStore_ja_tem_as_cinco_colunas", func(t *testing.T) {
		// NewMessageStore calls ensureMessagesSchema right after its own
		// CREATE TABLE IF NOT EXISTS, which already declares the 5 columns for
		// a brand-new database — this proves that path is a true no-op, not
		// silently failing to find anything to add.
		store := setupPollStore(t)
		got := messagesColumnNames(t, store.db)
		gotSet := make(map[string]bool, len(got))
		for _, name := range got {
			gotSet[name] = true
		}
		for _, want := range newColumns {
			if !gotSet[want] {
				t.Errorf("new store missing column %q; got %v", want, got)
			}
		}
	})
}

// TestStoreMessageSenderJID covers D8: the full sender JID handleMessage
// already computes reaches the sender_jid column via its own write path
// (StoreMessageSenderJID), without StoreMessage's signature or its
// COALESCE(NULLIF(...)) content-preservation semantics changing.
//
// The identifiers below are deliberately not phone-shaped (no digit run long
// enough to look like a real number) — the column is plain TEXT and the
// behavior under test (does the value reach the row, is it preserved when the
// next write has nothing new) does not need realistic-looking data. The
// "@s.whatsapp.net" suffix is kept (it is what a real JID looks like), with a
// non-numeric local part that cannot match a phone number.
func TestStoreMessageSenderJID(t *testing.T) {
	store := setupPollStore(t)
	chatJID := "grupo-de-teste@g.us"
	// messages.chat_jid has a FOREIGN KEY into chats(jid) — the row has to
	// exist before StoreMessage can insert against it.
	if err := store.StoreChat(chatJID, "Grupo de teste", time.Now()); err != nil {
		t.Fatalf("StoreChat: %v", err)
	}

	t.Run("grava_o_jid_completo_sem_mexer_em_StoreMessage", func(t *testing.T) {
		if err := store.StoreMessage("MSG-A", chatJID, "autor-a", "oi", time.Now(), false, "", "", "", nil, nil, nil, 0); err != nil {
			t.Fatalf("StoreMessage: %v", err)
		}
		if err := store.StoreMessageSenderJID("MSG-A", chatJID, "autor-a@s.whatsapp.net"); err != nil {
			t.Fatalf("StoreMessageSenderJID: %v", err)
		}

		var senderJID sql.NullString
		var content string
		if err := store.db.QueryRow("SELECT sender_jid, content FROM messages WHERE id = ? AND chat_jid = ?", "MSG-A", chatJID).Scan(&senderJID, &content); err != nil {
			t.Fatalf("query row: %v", err)
		}
		if !senderJID.Valid || senderJID.String != "autor-a@s.whatsapp.net" {
			t.Errorf("sender_jid = %v, want %q", senderJID, "autor-a@s.whatsapp.net")
		}
		if content != "oi" {
			t.Errorf("content = %q, want %q (StoreMessage's own write must be untouched)", content, "oi")
		}
	})

	t.Run("jid_vazio_nao_apaga_valor_ja_gravado", func(t *testing.T) {
		if err := store.StoreMessage("MSG-B", chatJID, "autor-b", "oi de novo", time.Now(), false, "", "", "", nil, nil, nil, 0); err != nil {
			t.Fatalf("StoreMessage: %v", err)
		}
		if err := store.StoreMessageSenderJID("MSG-B", chatJID, "autor-b@s.whatsapp.net"); err != nil {
			t.Fatalf("StoreMessageSenderJID (initial): %v", err)
		}
		if err := store.StoreMessageSenderJID("MSG-B", chatJID, ""); err != nil {
			t.Fatalf("StoreMessageSenderJID (empty): %v", err)
		}

		var senderJID sql.NullString
		if err := store.db.QueryRow("SELECT sender_jid FROM messages WHERE id = ? AND chat_jid = ?", "MSG-B", chatJID).Scan(&senderJID); err != nil {
			t.Fatalf("query row: %v", err)
		}
		if !senderJID.Valid || senderJID.String != "autor-b@s.whatsapp.net" {
			t.Errorf("sender_jid = %v, want the previously stored value preserved", senderJID)
		}
	})
}

// TestExtractContextInfo covers extractContextInfo (D1, D13): the first
// non-nil ContextInfo among the message types that can carry one, and the
// nil-safety that lets a nil msg or a plain Conversation (which has no
// ContextInfo at all) pass through as "not a reply" instead of panicking.
//
// Identifiers below are deliberately not phone-shaped, same convention as
// TestStoreMessageSenderJID above: the behavior under test doesn't need
// realistic-looking data.
func TestExtractContextInfo(t *testing.T) {
	t.Run("extended_text_com_citacao", func(t *testing.T) {
		msg := &waProto.Message{
			ExtendedTextMessage: &waProto.ExtendedTextMessage{
				Text: proto.String("respondendo"),
				ContextInfo: &waProto.ContextInfo{
					StanzaID:    proto.String("MSG-CITADA"),
					Participant: proto.String("autor-citado@s.whatsapp.net"),
				},
			},
		}
		ci := extractContextInfo(msg)
		if ci == nil {
			t.Fatal("extractContextInfo() = nil, want a ContextInfo")
		}
		if ci.GetStanzaID() != "MSG-CITADA" {
			t.Errorf("StanzaID = %q, want %q", ci.GetStanzaID(), "MSG-CITADA")
		}
		if ci.GetParticipant() != "autor-citado@s.whatsapp.net" {
			t.Errorf("Participant = %q, want %q", ci.GetParticipant(), "autor-citado@s.whatsapp.net")
		}
	})

	t.Run("conversation_pura_sem_contexto", func(t *testing.T) {
		msg := &waProto.Message{Conversation: proto.String("oi")}
		if ci := extractContextInfo(msg); ci != nil {
			t.Errorf("extractContextInfo() = %v, want nil (Conversation has no ContextInfo)", ci)
		}
	})

	t.Run("midia_com_citacao", func(t *testing.T) {
		msg := &waProto.Message{
			ImageMessage: &waProto.ImageMessage{
				Caption: proto.String("legenda"),
				ContextInfo: &waProto.ContextInfo{
					StanzaID:    proto.String("MSG-IMG-CITADA"),
					Participant: proto.String("autor-img@s.whatsapp.net"),
				},
			},
		}
		ci := extractContextInfo(msg)
		if ci == nil {
			t.Fatal("extractContextInfo() = nil, want a ContextInfo for an image message carrying a citation")
		}
		if ci.GetStanzaID() != "MSG-IMG-CITADA" {
			t.Errorf("StanzaID = %q, want %q", ci.GetStanzaID(), "MSG-IMG-CITADA")
		}
	})

	t.Run("nil", func(t *testing.T) {
		if ci := extractContextInfo(nil); ci != nil {
			t.Errorf("extractContextInfo(nil) = %v, want nil", ci)
		}
	})
}

// TestStoreMessageContext covers what handleMessage persists from a received
// ContextInfo (D1, D13): the quote fields and mentions land on the row when
// there's something to record, mentions is SQL NULL (never "[]" or "") when
// nobody's mentioned, and a nil ContextInfo issues no UPDATE at all.
//
// Identifiers below are deliberately not phone-shaped, same convention as
// TestStoreMessageSenderJID above.
func TestStoreMessageContext(t *testing.T) {
	store := setupPollStore(t)
	chatJID := "grupo-contexto@g.us"
	if err := store.StoreChat(chatJID, "Grupo de teste", time.Now()); err != nil {
		t.Fatalf("StoreChat: %v", err)
	}

	t.Run("grava_citacao_e_mencoes", func(t *testing.T) {
		if err := store.StoreMessage("MSG-CTX-A", chatJID, "quem-respondeu", "respondendo", time.Now(), false, "", "", "", nil, nil, nil, 0); err != nil {
			t.Fatalf("StoreMessage: %v", err)
		}
		ci := &waProto.ContextInfo{
			StanzaID:      proto.String("MSG-CITADA"),
			Participant:   proto.String("autor-citado@s.whatsapp.net"),
			QuotedMessage: &waProto.Message{Conversation: proto.String("texto citado")},
			MentionedJID:  []string{"contato-mencionado@s.whatsapp.net"},
		}
		if err := store.StoreMessageContext("MSG-CTX-A", chatJID, ci); err != nil {
			t.Fatalf("StoreMessageContext: %v", err)
		}

		var quotedID, quotedSender, quotedContent, mentions sql.NullString
		if err := store.db.QueryRow(
			"SELECT quoted_message_id, quoted_sender, quoted_content, mentions FROM messages WHERE id = ? AND chat_jid = ?",
			"MSG-CTX-A", chatJID,
		).Scan(&quotedID, &quotedSender, &quotedContent, &mentions); err != nil {
			t.Fatalf("query row: %v", err)
		}
		if quotedID.String != "MSG-CITADA" {
			t.Errorf("quoted_message_id = %v, want %q", quotedID, "MSG-CITADA")
		}
		if quotedSender.String != "autor-citado@s.whatsapp.net" {
			t.Errorf("quoted_sender = %v, want %q", quotedSender, "autor-citado@s.whatsapp.net")
		}
		if quotedContent.String != "texto citado" {
			t.Errorf("quoted_content = %v, want %q", quotedContent, "texto citado")
		}
		var got []string
		if err := json.Unmarshal([]byte(mentions.String), &got); err != nil {
			t.Fatalf("mentions is not valid JSON: %v (%v)", mentions, err)
		}
		if len(got) != 1 || got[0] != "contato-mencionado@s.whatsapp.net" {
			t.Errorf("mentions = %v, want [%q]", got, "contato-mencionado@s.whatsapp.net")
		}
	})

	t.Run("sem_mencao_grava_NULL_nao_array_vazio", func(t *testing.T) {
		if err := store.StoreMessage("MSG-CTX-B", chatJID, "quem-respondeu", "respondendo sem arroba", time.Now(), false, "", "", "", nil, nil, nil, 0); err != nil {
			t.Fatalf("StoreMessage: %v", err)
		}
		ci := &waProto.ContextInfo{
			StanzaID:    proto.String("MSG-CITADA-B"),
			Participant: proto.String("autor-citado@s.whatsapp.net"),
		}
		if err := store.StoreMessageContext("MSG-CTX-B", chatJID, ci); err != nil {
			t.Fatalf("StoreMessageContext: %v", err)
		}

		var mentions sql.NullString
		if err := store.db.QueryRow("SELECT mentions FROM messages WHERE id = ? AND chat_jid = ?", "MSG-CTX-B", chatJID).Scan(&mentions); err != nil {
			t.Fatalf("query row: %v", err)
		}
		if mentions.Valid {
			t.Errorf("mentions = %q, want SQL NULL (not \"[]\" or \"\")", mentions.String)
		}
	})

	t.Run("sem_contexto_nao_faz_UPDATE", func(t *testing.T) {
		if err := store.StoreMessage("MSG-CTX-C", chatJID, "quem-mandou", "mensagem normal", time.Now(), false, "", "", "", nil, nil, nil, 0); err != nil {
			t.Fatalf("StoreMessage: %v", err)
		}
		if err := store.StoreMessageContext("MSG-CTX-C", chatJID, nil); err != nil {
			t.Fatalf("StoreMessageContext(nil): %v", err)
		}
		var quotedID sql.NullString
		if err := store.db.QueryRow("SELECT quoted_message_id FROM messages WHERE id = ? AND chat_jid = ?", "MSG-CTX-C", chatJID).Scan(&quotedID); err != nil {
			t.Fatalf("query row: %v", err)
		}
		if quotedID.Valid {
			t.Errorf("quoted_message_id = %q, want NULL (StoreMessageContext(nil) must not UPDATE)", quotedID.String)
		}
	})
}

// TestSendQuotedRecusa covers the three D9/D12 send-side refusals resolved in
// buildQuoteContextInfo (called from sendWhatsAppMessage): none of them may
// reach client.SendMessage. client is passed as nil throughout — the citation
// is resolved, and on refusal returned, before the function ever touches the
// client. A nil client not panicking is itself part of the proof nothing was
// sent: if the citation check regressed to run after the client is touched,
// IsConnected()/SendMessage() would need a real client, and this test would
// have to be rewritten to supply one.
//
// Identifiers below are deliberately not phone-shaped, same convention as
// TestStoreMessageSenderJID above.
func TestSendQuotedRecusa(t *testing.T) {
	t.Run("id_inexistente_nao_envia", func(t *testing.T) {
		store := setupPollStore(t)

		ok, msg, status, _ := sendWhatsAppMessage(nil, store, "contato-teste@s.whatsapp.net", "oi", "", "MSG-NAO-EXISTE", nil)
		if ok {
			t.Fatalf("sendWhatsAppMessage() ok = true, want false; msg=%q", msg)
		}
		if status < 400 || status >= 500 {
			t.Fatalf("status = %d, want 4xx; msg=%q", status, msg)
		}
	})

	t.Run("autor_desconhecido_recusa", func(t *testing.T) {
		store := setupPollStore(t)
		chatJID := "grupo-autor-desconhecido@g.us"
		if err := store.StoreChat(chatJID, "Grupo de teste", time.Now()); err != nil {
			t.Fatalf("StoreChat: %v", err)
		}
		// sender_jid is never written (StoreMessageSenderJID not called) — D9's
		// "sender_jid nulo/vazio" case.
		if err := store.StoreMessage("MSG-AUTOR-DESCONHECIDO", chatJID, "alguem", "conteudo", time.Now(), false, "", "", "", nil, nil, nil, 0); err != nil {
			t.Fatalf("StoreMessage: %v", err)
		}

		ok, msg, status, _ := sendWhatsAppMessage(nil, store, chatJID, "respondendo", "", "MSG-AUTOR-DESCONHECIDO", nil)
		if ok {
			t.Fatalf("sendWhatsAppMessage() ok = true, want false; msg=%q", msg)
		}
		if status < 400 || status >= 500 {
			t.Fatalf("status = %d, want 4xx; msg=%q", status, msg)
		}
		if !strings.Contains(msg, "unknown") {
			t.Errorf("message = %q, want it to say the author is unknown", msg)
		}
	})

	t.Run("chat_divergente_recusa", func(t *testing.T) {
		store := setupPollStore(t)
		chatA := "chat-a@g.us"
		chatB := "chat-b@g.us"
		if err := store.StoreChat(chatA, "Chat A", time.Now()); err != nil {
			t.Fatalf("StoreChat chatA: %v", err)
		}
		if err := store.StoreChat(chatB, "Chat B", time.Now()); err != nil {
			t.Fatalf("StoreChat chatB: %v", err)
		}
		if err := store.StoreMessage("MSG-EM-CHAT-A", chatA, "autor-a", "conteudo em A", time.Now(), false, "", "", "", nil, nil, nil, 0); err != nil {
			t.Fatalf("StoreMessage: %v", err)
		}
		if err := store.StoreMessageSenderJID("MSG-EM-CHAT-A", chatA, "autor-a@s.whatsapp.net"); err != nil {
			t.Fatalf("StoreMessageSenderJID: %v", err)
		}

		// MSG-EM-CHAT-A lives in chatA; citing it while sending to chatB must
		// be refused — GetMessageForQuote scopes the lookup by (id, chat_jid).
		ok, msg, status, _ := sendWhatsAppMessage(nil, store, chatB, "respondendo", "", "MSG-EM-CHAT-A", nil)
		if ok {
			t.Fatalf("sendWhatsAppMessage() ok = true, want false; msg=%q", msg)
		}
		if status < 400 || status >= 500 {
			t.Fatalf("status = %d, want 4xx; msg=%q", status, msg)
		}
	})
}

// eightPlusDigits matches any run of 8 or more digits — D6's ban on a phone
// number or JID surfacing in an ambiguous-mention response.
var eightPlusDigits = regexp.MustCompile(`[0-9]{8,}`)

// TestResolveMentionAmbigua covers task 5's mention resolution (D3-D7)
// directly against a hand-built participants list — no live whatsmeow client
// needed, per resolveMentionsAgainstParticipants' doc comment. Identifiers
// are deliberately not phone-shaped, same convention as TestSendQuotedRecusa.
// chatDeTeste é o chat de destino usado pelos testes de menção. Um ref (D6)
// só resolve no chat que o gerou, então o valor precisa ser o mesmo na
// geração e no reenvio.
const chatDeTeste = "grupo-de-teste@g.us"

func TestResolveMentionAmbigua(t *testing.T) {
	t.Run("dois_candidatos_recusa_e_devolve_refs", func(t *testing.T) {
		// Two participants sharing the same first name (D6's "dois 'Rodrigo'
		// no grupo") — matched via first_name, so their differing full_name
		// doesn't dodge the ambiguity.
		participants := []mentionParticipant{
			{jid: "participante-a@s.whatsapp.net", phoneUser: "participante-a", firstName: "Rodrigo", fullName: "Rodrigo Alfa"},
			{jid: "participante-b@s.whatsapp.net", phoneUser: "participante-b", firstName: "Rodrigo", fullName: "Rodrigo Beta"},
		}
		resolved, _, candidates, errMsg, statusCode := resolveMentionsAgainstParticipants(participants, []string{"Rodrigo"}, chatDeTeste)
		if resolved != nil {
			t.Fatalf("resolved = %v, want nil (nothing should be ready to send)", resolved)
		}
		if errMsg == "" || statusCode < 400 || statusCode >= 500 {
			t.Fatalf("errMsg=%q statusCode=%d, want a 4xx refusal", errMsg, statusCode)
		}
		if len(candidates) != 2 {
			t.Fatalf("len(candidates) = %d, want 2", len(candidates))
		}
		if candidates[0].Ref == candidates[1].Ref {
			t.Fatalf("candidates share the same ref %q, want two distinct refs", candidates[0].Ref)
		}
		body, err := json.Marshal(struct {
			Message    string                     `json:"message"`
			Candidates []MentionCandidateResponse `json:"candidates"`
		}{Message: errMsg, Candidates: candidates})
		if err != nil {
			t.Fatalf("json.Marshal: %v", err)
		}
		if eightPlusDigits.Match(body) {
			t.Fatalf("response body has a run of 8+ digits (looks like a phone number): %s", body)
		}
	})

	t.Run("casamento_unico_substitui_texto_e_seta_mentionedjid", func(t *testing.T) {
		participants := []mentionParticipant{
			{jid: "participante-c@s.whatsapp.net", phoneUser: "participante-c", pushName: "Ana"},
		}
		resolved, outrosNomes, candidates, errMsg, statusCode := resolveMentionsAgainstParticipants(participants, []string{"Ana"}, chatDeTeste)
		if errMsg != "" || statusCode != 0 {
			t.Fatalf("errMsg=%q statusCode=%d, want no refusal", errMsg, statusCode)
		}
		if candidates != nil {
			t.Fatalf("candidates = %v, want nil for a single match", candidates)
		}
		if len(resolved) != 1 {
			t.Fatalf("len(resolved) = %d, want 1", len(resolved))
		}
		text, mentionedJIDs, ancoraErr, _ := applyMentions("oi @Ana tudo bem?", resolved, outrosNomes)
		if ancoraErr != "" {
			t.Fatalf("unexpected anchor refusal: %v", ancoraErr)
		}
		if !strings.Contains(text, "@participante-c") {
			t.Fatalf("text = %q, want it to contain the substituted @participante-c", text)
		}
		if strings.Contains(text, "@Ana") {
			t.Fatalf("text = %q, want @Ana replaced, not left in place", text)
		}
		if len(mentionedJIDs) != 1 || mentionedJIDs[0] != "participante-c@s.whatsapp.net" {
			t.Fatalf("mentionedJIDs = %v, want [participante-c@s.whatsapp.net]", mentionedJIDs)
		}
	})

	t.Run("zero_casamentos_recusa_sem_enviar", func(t *testing.T) {
		participants := []mentionParticipant{
			{jid: "participante-c@s.whatsapp.net", phoneUser: "participante-c", pushName: "Ana"},
		}
		resolved, _, candidates, errMsg, statusCode := resolveMentionsAgainstParticipants(participants, []string{"NomeQueNaoEstaNoChat"}, chatDeTeste)
		if resolved != nil || candidates != nil {
			t.Fatalf("resolved=%v candidates=%v, want both nil", resolved, candidates)
		}
		if errMsg == "" || statusCode < 400 || statusCode >= 500 {
			t.Fatalf("errMsg=%q statusCode=%d, want a 4xx refusal", errMsg, statusCode)
		}
	})

	t.Run("arroba_nao_listado_sobrevive_intacto", func(t *testing.T) {
		participants := []mentionParticipant{
			{jid: "participante-c@s.whatsapp.net", phoneUser: "participante-c", pushName: "Ana"},
		}
		resolved, outrosNomes, _, errMsg, _ := resolveMentionsAgainstParticipants(participants, []string{"Ana"}, chatDeTeste)
		if errMsg != "" {
			t.Fatalf("unexpected refusal: %v", errMsg)
		}
		// "@contato-fake.exemplo" is never listed in mentions — D5 says the
		// ponte never scans the text for it, only for the names it was asked
		// to substitute.
		text, _, ancoraErr, _ := applyMentions("fala com @contato-fake.exemplo e com @Ana", resolved, outrosNomes)
		if ancoraErr != "" {
			t.Fatalf("unexpected anchor refusal: %v", ancoraErr)
		}
		if !strings.Contains(text, "@contato-fake.exemplo") {
			t.Fatalf("text = %q, want the unlisted @contato-fake.exemplo left intact", text)
		}
	})

	t.Run("ref_expirado_recusa", func(t *testing.T) {
		mentionRefs.Lock()
		mentionRefs.byRef["expirado1"] = mentionRefEntry{
			// chatJID igual ao consultado de proposito: sem ele, a trava de chat
			// recusaria sozinha e o teste ficaria verde mesmo com a checagem de
			// TTL removida (achado da revisao de 2026-09-09).
			jid: "participante-a@s.whatsapp.net", name: "Rodrigo", chatJID: chatDeTeste, expiresAt: time.Now().Add(-time.Minute),
		}
		mentionRefs.Unlock()

		// Participante presente de proposito: sem ele, isChatParticipant
		// recusaria sozinho e o teste ficaria verde com a checagem de TTL
		// removida (achado da revisao de 2026-09-09, rodada 3).
		participantes := []mentionParticipant{
			{jid: "participante-a@s.whatsapp.net", phoneUser: "participante-a", firstName: "Rodrigo"},
		}
		resolved, _, candidates, errMsg, statusCode := resolveMentionsAgainstParticipants(participantes, []string{"ref:expirado1"}, chatDeTeste)
		if resolved != nil || candidates != nil {
			t.Fatalf("resolved=%v candidates=%v, want both nil", resolved, candidates)
		}
		if errMsg == "" || statusCode < 400 || statusCode >= 500 {
			t.Fatalf("errMsg=%q statusCode=%d, want a 4xx refusal", errMsg, statusCode)
		}
	})

	t.Run("ref_desconhecido_recusa", func(t *testing.T) {
		resolved, _, candidates, errMsg, statusCode := resolveMentionsAgainstParticipants(nil, []string{"ref:nunca-existiu"}, chatDeTeste)
		if resolved != nil || candidates != nil {
			t.Fatalf("resolved=%v candidates=%v, want both nil", resolved, candidates)
		}
		if errMsg == "" || statusCode < 400 || statusCode >= 500 {
			t.Fatalf("errMsg=%q statusCode=%d, want a 4xx refusal", errMsg, statusCode)
		}
	})
}

// TestAPIMessageWireShape locks the JSON wire shape of the four fields task 7
// added to APIMessage: quoted_message_id/quoted_sender/quoted_content have no
// omitempty (present as null when absent), mentions has omitempty (absent
// entirely when there are none).
func TestAPIMessageWireShape(t *testing.T) {
	t.Run("sem_citacao_e_sem_mencao", func(t *testing.T) {
		msg := APIMessage{
			Timestamp: time.Date(2026, 9, 8, 12, 0, 0, 0, time.UTC),
			Sender:    "remetente-teste",
			Content:   "oi",
			ChatJID:   "chat-teste@s.whatsapp.net",
			ID:        "MSG-1",
		}
		body, err := json.Marshal(msg)
		if err != nil {
			t.Fatalf("json.Marshal: %v", err)
		}
		var got map[string]interface{}
		if err := json.Unmarshal(body, &got); err != nil {
			t.Fatalf("json.Unmarshal: %v", err)
		}
		for _, field := range []string{"quoted_message_id", "quoted_sender", "quoted_content"} {
			v, ok := got[field]
			if !ok {
				t.Errorf("%s missing from wire shape, want present (null)", field)
			} else if v != nil {
				t.Errorf("%s = %v, want null", field, v)
			}
		}
		if v, ok := got["mentions"]; ok {
			t.Errorf("mentions present in wire shape = %v, want omitted (omitempty, no mentions)", v)
		}
	})

	t.Run("com_citacao_e_mencao", func(t *testing.T) {
		quotedID := "MSG-CITADA"
		quotedSender := "autor-citado@s.whatsapp.net"
		quotedContent := "texto citado"
		msg := APIMessage{
			Timestamp:       time.Date(2026, 9, 8, 12, 0, 0, 0, time.UTC),
			Sender:          "remetente-teste",
			Content:         "respondendo",
			ChatJID:         "chat-teste@s.whatsapp.net",
			ID:              "MSG-2",
			QuotedMessageID: &quotedID,
			QuotedSender:    &quotedSender,
			QuotedContent:   &quotedContent,
			Mentions:        []string{"contato-mencionado@s.whatsapp.net"},
		}
		body, err := json.Marshal(msg)
		if err != nil {
			t.Fatalf("json.Marshal: %v", err)
		}
		var got APIMessage
		if err := json.Unmarshal(body, &got); err != nil {
			t.Fatalf("json.Unmarshal: %v", err)
		}
		if got.QuotedMessageID == nil || *got.QuotedMessageID != quotedID {
			t.Errorf("QuotedMessageID = %v, want %q", got.QuotedMessageID, quotedID)
		}
		if got.QuotedSender == nil || *got.QuotedSender != quotedSender {
			t.Errorf("QuotedSender = %v, want %q", got.QuotedSender, quotedSender)
		}
		if got.QuotedContent == nil || *got.QuotedContent != quotedContent {
			t.Errorf("QuotedContent = %v, want %q", got.QuotedContent, quotedContent)
		}
		if len(got.Mentions) != 1 || got.Mentions[0] != "contato-mencionado@s.whatsapp.net" {
			t.Errorf("Mentions = %v, want [contato-mencionado@s.whatsapp.net]", got.Mentions)
		}
	})
}

// TestQueriesFeedingScanAPIMessageRowIncludeQuotedAndMentions is task 7's
// column-order proof. scanAPIMessageRow (and the manual scan of
// getMessageContext's target row) expects quoted_message_id, quoted_sender,
// quoted_content, mentions appended, in that exact order, at the end of the
// SELECT list. A query that feeds it out of order does not fail to compile —
// it silently scans the wrong Go field from the wrong SQL column at runtime.
// This drives all five call sites (listMessages; getMessageContext's target,
// before and after; getLastInteraction) against one real row written through
// the production StoreMessage + StoreMessageContext write path, and checks
// the quoted/mentions fields come back scanned correctly on each.
func TestQueriesFeedingScanAPIMessageRowIncludeQuotedAndMentions(t *testing.T) {
	store := setupPollStore(t)
	chatJID := "grupo-leitura@g.us"
	if err := store.StoreChat(chatJID, "Grupo de leitura", time.Now()); err != nil {
		t.Fatalf("StoreChat: %v", err)
	}

	// UTC explicitly: getMessageContext's before/after queries re-bind the
	// timestamp it just scanned back from the target row as a query
	// parameter, and a value carrying a non-UTC offset round-trips through
	// modernc's sqlite driver with a different text rendering than what was
	// stored, which corrupts the plain-text ">"/"<" comparison SQLite does
	// on a TIMESTAMP column. UTC avoids that pre-existing quirk; it is not
	// part of task 7's scope to fix.
	beforeTS := time.Now().UTC().Add(-time.Hour)
	targetTS := time.Now().UTC()
	afterTS := time.Now().UTC().Add(time.Hour)

	if err := store.StoreMessage("MSG-BEFORE", chatJID, "quem-mandou", "mensagem antes", beforeTS, false, "", "", "", nil, nil, nil, 0); err != nil {
		t.Fatalf("StoreMessage(before): %v", err)
	}
	if err := store.StoreMessage("MSG-TARGET", chatJID, "quem-respondeu", "respondendo", targetTS, false, "", "", "", nil, nil, nil, 0); err != nil {
		t.Fatalf("StoreMessage(target): %v", err)
	}
	if err := store.StoreMessage("MSG-AFTER", chatJID, "quem-mandou", "mensagem depois", afterTS, false, "", "", "", nil, nil, nil, 0); err != nil {
		t.Fatalf("StoreMessage(after): %v", err)
	}

	ci := &waProto.ContextInfo{
		StanzaID:      proto.String("MSG-CITADA-LEITURA"),
		Participant:   proto.String("autor-citado-leitura@s.whatsapp.net"),
		QuotedMessage: &waProto.Message{Conversation: proto.String("texto citado leitura")},
		MentionedJID:  []string{"contato-mencionado-leitura@s.whatsapp.net"},
	}
	for _, id := range []string{"MSG-BEFORE", "MSG-TARGET", "MSG-AFTER"} {
		if err := store.StoreMessageContext(id, chatJID, ci); err != nil {
			t.Fatalf("StoreMessageContext(%s): %v", id, err)
		}
	}

	checkQuoted := func(t *testing.T, label string, msg APIMessage) {
		t.Helper()
		if msg.QuotedMessageID == nil || *msg.QuotedMessageID != "MSG-CITADA-LEITURA" {
			t.Errorf("%s: QuotedMessageID = %v, want MSG-CITADA-LEITURA", label, msg.QuotedMessageID)
		}
		if msg.QuotedSender == nil || *msg.QuotedSender != "autor-citado-leitura@s.whatsapp.net" {
			t.Errorf("%s: QuotedSender = %v, want autor-citado-leitura@s.whatsapp.net", label, msg.QuotedSender)
		}
		if msg.QuotedContent == nil || *msg.QuotedContent != "texto citado leitura" {
			t.Errorf("%s: QuotedContent = %v, want %q", label, msg.QuotedContent, "texto citado leitura")
		}
		if len(msg.Mentions) != 1 || msg.Mentions[0] != "contato-mencionado-leitura@s.whatsapp.net" {
			t.Errorf("%s: Mentions = %v, want [contato-mencionado-leitura@s.whatsapp.net]", label, msg.Mentions)
		}
	}

	t.Run("listMessages", func(t *testing.T) {
		resp, err := listMessages(store.db, MessagesRequest{ChatJID: &chatJID, Limit: 10})
		if err != nil {
			t.Fatalf("listMessages: %v", err)
		}
		var found *APIMessage
		for i := range resp.Messages {
			if resp.Messages[i].ID == "MSG-TARGET" {
				found = &resp.Messages[i]
			}
		}
		if found == nil {
			t.Fatal("MSG-TARGET not found in listMessages result")
		}
		checkQuoted(t, "listMessages", *found)
	})

	t.Run("getMessageContext", func(t *testing.T) {
		resp, ok, err := getMessageContext(store.db, MessageContextRequest{MessageID: "MSG-TARGET", Before: 5, After: 5})
		if err != nil {
			t.Fatalf("getMessageContext: %v", err)
		}
		if !ok {
			t.Fatal("getMessageContext: message not found")
		}
		checkQuoted(t, "getMessageContext.target", resp.Message)
		if len(resp.Before) != 1 {
			t.Fatalf("getMessageContext.Before = %d messages, want 1", len(resp.Before))
		}
		checkQuoted(t, "getMessageContext.before", resp.Before[0])
		if len(resp.After) != 1 {
			t.Fatalf("getMessageContext.After = %d messages, want 1", len(resp.After))
		}
		checkQuoted(t, "getMessageContext.after", resp.After[0])
	})

	t.Run("getLastInteraction", func(t *testing.T) {
		resp, err := getLastInteraction(store.db, chatJID)
		if err != nil {
			t.Fatalf("getLastInteraction: %v", err)
		}
		if resp.Message == nil {
			t.Fatal("getLastInteraction: nil message")
		}
		if resp.Message.ID != "MSG-AFTER" {
			t.Fatalf("getLastInteraction returned %q, want MSG-AFTER (most recent)", resp.Message.ID)
		}
		checkQuoted(t, "getLastInteraction", *resp.Message)
	})
}

// TestIsUnknownAuthorPrivado guards the regression found in production on
// 2026-09-09: in a 1:1 chat the sender's user part IS the chat's user part for
// every message, so the group heuristic (D8's 9,433 rows where the group JID
// got written as the author) refused every legitimate quote in a direct
// conversation. The heuristic is group-only; outside a group, only a missing
// sender_jid makes an author unknown.
func TestIsUnknownAuthorPrivado(t *testing.T) {
	cases := []struct {
		nome      string
		senderJID string
		sender    string
		chatJID   string
		want      bool
	}{
		{"privado com sender_jid: autor conhecido", "contato-a@s.whatsapp.net", "contato-a", "contato-a@s.whatsapp.net", false},
		{"privado sem sender_jid: autor desconhecido", "", "contato-a", "contato-a@s.whatsapp.net", true},
		{"grupo com autor real: conhecido", "contato-a@s.whatsapp.net", "contato-a", "grupo-x@g.us", false},
		{"grupo com o proprio grupo como autor: desconhecido", "grupo-x@s.whatsapp.net", "grupo-x", "grupo-x@g.us", true},
		{"grupo sem sender_jid: desconhecido", "", "contato-a", "grupo-x@g.us", true},
	}
	for _, c := range cases {
		t.Run(c.nome, func(t *testing.T) {
			if got := isUnknownAuthor(c.senderJID, c.sender, c.chatJID); got != c.want {
				t.Fatalf("isUnknownAuthor(%q, %q, %q) = %v, want %v", c.senderJID, c.sender, c.chatJID, got, c.want)
			}
		})
	}
}

// TestRefDeOutroChatNaoResolve cobre o achado 2 da revisão de 2026-09-09: o
// ramo "ref:" resolvia sem olhar o chat, então um ref gerado no chat A
// mencionava a pessoa no chat B — escrevendo o número dela no texto enviado a
// B, para gente que nunca esteve naquela conversa.
func TestRefDeOutroChatNaoResolve(t *testing.T) {
	participants := []mentionParticipant{
		{jid: "contato-a@s.whatsapp.net", phoneUser: "contato-a", firstName: "Rodrigo", fullName: "Rodrigo Um"},
		{jid: "contato-b@s.whatsapp.net", phoneUser: "contato-b", firstName: "Rodrigo", fullName: "Rodrigo Dois"},
	}
	_, _, candidates, _, status := resolveMentionsAgainstParticipants(participants, []string{"Rodrigo"}, chatDeTeste)
	if status != http.StatusBadRequest || len(candidates) != 2 {
		t.Fatalf("esperava recusa ambigua com 2 candidatos, veio status=%d candidatos=%d", status, len(candidates))
	}
	ref := "ref:" + candidates[0].Ref

	t.Run("no chat que gerou, resolve", func(t *testing.T) {
		resolved, _, _, errMsg, status := resolveMentionsAgainstParticipants(participants, []string{ref}, chatDeTeste)
		if errMsg != "" || status != 0 || len(resolved) != 1 {
			t.Fatalf("esperava resolver no chat de origem, veio errMsg=%q status=%d resolved=%d", errMsg, status, len(resolved))
		}
	})

	t.Run("em outro chat, recusa e nao resolve ninguem", func(t *testing.T) {
		outroChat := "outro-grupo@g.us"
		// A pessoa do ref participa TAMBEM deste outro chat, de proposito: se
		// ela nao participasse, isChatParticipant recusaria sozinho e este
		// teste ficaria verde mesmo com a fixacao por chat removida — que e o
		// unico mecanismo que ele existe para medir (achado da revisao de
		// 2026-09-09, rodada 3).
		outrosParticipantes := append([]mentionParticipant{
			{jid: "contato-c@s.whatsapp.net", phoneUser: "contato-c", firstName: "Carla", fullName: "Carla Tres"},
		}, participants...)
		resolved, _, _, errMsg, status := resolveMentionsAgainstParticipants(outrosParticipantes, []string{ref}, outroChat)
		if status != http.StatusBadRequest || errMsg == "" {
			t.Fatalf("esperava 4xx recusando o ref de outro chat, veio status=%d errMsg=%q", status, errMsg)
		}
		if len(resolved) != 0 {
			t.Fatalf("nenhuma mencao pode ser resolvida na recusa, veio %d", len(resolved))
		}
	})
}

// TestSweepDeRefsExpirados cobre o achado 3: a única remoção acontecia quando
// alguém consultava um ref já expirado, então ref gerado e nunca reenviado
// ficava no mapa para sempre.
func TestSweepDeRefsExpirados(t *testing.T) {
	// Preserva e restaura em vez de zerar: o mapa e global, e zera-lo aqui
	// apagava os refs cunhados por TestRefDeOutroChatNaoResolve — verde so pela
	// ordem do arquivo, flake com -shuffle=on (achado da revisao de 2026-09-09).
	mentionRefs.Lock()
	anterior := mentionRefs.byRef
	mentionRefs.byRef = make(map[string]mentionRefEntry)
	for k, v := range anterior {
		mentionRefs.byRef[k] = v
	}
	t.Cleanup(func() {
		mentionRefs.Lock()
		defer mentionRefs.Unlock()
		mentionRefs.byRef = anterior
	})
	mentionRefs.byRef["velho1"] = mentionRefEntry{jid: "contato-x@s.whatsapp.net", name: "X", chatJID: chatDeTeste, expiresAt: time.Now().Add(-time.Hour)}
	mentionRefs.byRef["velho2"] = mentionRefEntry{jid: "contato-y@s.whatsapp.net", name: "Y", chatJID: chatDeTeste, expiresAt: time.Now().Add(-time.Minute)}
	mentionRefs.Unlock()

	novo := storeMentionRef("contato-z@s.whatsapp.net", "Z", chatDeTeste)

	mentionRefs.Lock()
	defer mentionRefs.Unlock()
	if _, existe := mentionRefs.byRef["velho1"]; existe {
		t.Error("ref expirado velho1 continuou no mapa depois de um insert")
	}
	if _, existe := mentionRefs.byRef["velho2"]; existe {
		t.Error("ref expirado velho2 continuou no mapa depois de um insert")
	}
	if _, existe := mentionRefs.byRef[novo]; !existe {
		t.Error("o ref recem-criado sumiu no sweep")
	}
}

// TestRefReenviaComONomeQueAPonteMostrou cobre o caminho feliz da D6, que nao
// tinha bateria nenhuma (observacao 3 da rodada 5): a recusa ambigua devolve
// `candidates[].Nome`, o autor reescreve o texto com esse nome, e o reenvio por
// ref tem de sair. Cobre tambem a observacao 1: o ref guarda o `raw` pedido
// ("Luis", sem acento), enquanto o texto tem o nome do participante ("Luís") —
// e nome compartilhado por dois participantes so liga quando o usuario ja
// desambiguou, que e exatamente este caso.
func TestRefReenviaComONomeQueAPonteMostrou(t *testing.T) {
	participants := []mentionParticipant{
		{jid: "contato-um@s.whatsapp.net", phoneUser: "contato-um", firstName: "Luís", fullName: "Luís Um"},
		{jid: "contato-dois@s.whatsapp.net", phoneUser: "contato-dois", firstName: "Luís", fullName: "Luís Dois"},
	}
	_, _, candidates, _, status := resolveMentionsAgainstParticipants(participants, []string{"Luis"}, chatDeTeste)
	if status != http.StatusBadRequest || len(candidates) != 2 {
		t.Fatalf("esperava recusa ambigua com 2 candidatos, veio status=%d candidatos=%d", status, len(candidates))
	}
	// Os dois rotulos tem de ser DISTINGUIVEIS: rotular pelo campo que casou
	// fazia os dois homonimos sairem como "Luís", e a pergunta da D6 ficava
	// impossivel de responder (achado da rodada 7).
	if candidates[0].Nome == candidates[1].Nome {
		t.Fatalf("os dois candidatos saem como %q — a pergunta da D6 nao da para responder", candidates[0].Nome)
	}
	if candidates[1].Nome != "Luís Dois" {
		t.Fatalf("candidates[1].Nome = %q, want the most specific name known", candidates[1].Nome)
	}

	// O autor reescreve o texto com o nome que a ponte mostrou.
	resolved, outrosNomes, _, errMsg, _ := resolveMentionsAgainstParticipants(
		participants, []string{"ref:" + candidates[1].Ref}, chatDeTeste)
	if errMsg != "" {
		t.Fatalf("unexpected refusal resolving the ref: %v", errMsg)
	}
	texto, jids, ancoraErr, _ := applyMentions("bom dia @Luís", resolved, outrosNomes)
	if ancoraErr != "" {
		t.Fatalf("unexpected anchor refusal: %v", ancoraErr)
	}
	if texto != "bom dia @contato-dois" {
		t.Fatalf("texto = %q, want the chosen candidate substituted", texto)
	}
	if len(jids) != 1 || jids[0] != "contato-dois@s.whatsapp.net" {
		t.Fatalf("jids = %v, want only the chosen candidate", jids)
	}
}

// TestNomeCompartilhadoNaoLigaSemDesambiguacao cobre a observacao 2 da rodada 5:
// o conserto da rodada 4 ligou nome de participante a mencao pedida pelo JID, e
// isso reintroduzia, pelo texto, a escolha que a D6 existe para nao deixar a
// ponte fazer sozinha — quando o MESMO nome pertence a dois participantes.
func TestNomeCompartilhadoNaoLigaSemDesambiguacao(t *testing.T) {
	ana := resolvedMention{name: "Ana", phoneUser: "ana-user", jid: "ana@s.whatsapp.net"}
	// "Ana Paula" e nome da propria Ana E de uma terceira pessoa.
	outros := []nomeDeParticipante{
		{nome: "Ana Paula", jid: ana.jid},
		{nome: "Ana Paula", jid: "beatriz@s.whatsapp.net"},
	}
	texto, jids, errMsg, _ := applyMentions("bom dia @Ana Paula, avisa a @Ana", []resolvedMention{ana}, outros)
	if errMsg != "" {
		t.Fatalf("unexpected refusal: %v", errMsg)
	}
	if texto != "bom dia @Ana Paula, avisa a @ana-user" {
		t.Fatalf("texto = %q, want the shared name left intact", texto)
	}
	if len(jids) != 1 || jids[0] != ana.jid {
		t.Fatalf("jids = %v", jids)
	}

	// Com desambiguacao explicita (viaRef), o mesmo nome compartilhado liga.
	anaViaRef := ana
	anaViaRef.viaRef = true
	texto, _, errMsg, _ = applyMentions("bom dia @Ana Paula", []resolvedMention{anaViaRef}, outros)
	if errMsg != "" {
		t.Fatalf("unexpected refusal after explicit disambiguation: %v", errMsg)
	}
	if texto != "bom dia @ana-user" {
		t.Fatalf("texto = %q, want the shared name substituted once the user disambiguated", texto)
	}
}

// TestDoisHomonimosPorRefNaMesmaMensagem cobre a observacao 1 da rodada 6: a
// varredura casava sempre o primeiro candidato da lista ordenada, entao as duas
// ancoras "@Luís" viravam a MESMA pessoa, a segunda mencao ficava sem uso, e a
// ponte recusava dizendo que a ancora nao existe — com ela escrita duas vezes.
// Caminho que o README ensina, e que ficava impossivel de completar.
func TestDoisHomonimosPorRefNaMesmaMensagem(t *testing.T) {
	um := resolvedMention{name: "Luis", phoneUser: "c-um", jid: "c-um@s.whatsapp.net", viaRef: true}
	dois := resolvedMention{name: "Luis", phoneUser: "c-dois", jid: "c-dois@s.whatsapp.net", viaRef: true}
	outros := []nomeDeParticipante{
		{nome: "Luís", jid: um.jid},
		{nome: "Luís", jid: dois.jid},
	}
	texto, jids, errMsg, _ := applyMentions("bom dia @Luís e @Luís", []resolvedMention{um, dois}, outros)
	if errMsg != "" {
		t.Fatalf("unexpected refusal: %v", errMsg)
	}
	if texto != "bom dia @c-um e @c-dois" {
		t.Fatalf("texto = %q, want one anchor per person", texto)
	}
	if len(jids) != 2 {
		t.Fatalf("jids = %v, want both mentions", jids)
	}
}

// TestNomeLongoUsadoNaoCaiEmNomeCurtoDeOutro cobre o bloqueante da rodada 7,
// que foi regressao da rodada 6: a preferencia por "candidato ainda nao usado"
// valia ENTRE COMPRIMENTOS DIFERENTES, entao "@Luis Montes" — cujo casamento
// longo ja estava usado — caia num "Luis" de OUTRA pessoa. O texto saia com o
// numero do B onde o autor escreveu o nome do A, e sem recusa: uma recusa
// segura virava um envio errado, que e o dano que a D6 existe para impedir.
func TestNomeLongoUsadoNaoCaiEmNomeCurtoDeOutro(t *testing.T) {
	a := resolvedMention{name: "Luis", phoneUser: "c-a", jid: "c-a@s.whatsapp.net", viaRef: true}
	b := resolvedMention{name: "Luis", phoneUser: "c-b", jid: "c-b@s.whatsapp.net", viaRef: true}
	outros := []nomeDeParticipante{
		{nome: "Luis Montes", jid: a.jid},
		{nome: "Luis", jid: a.jid},
		{nome: "Luis Silva", jid: b.jid},
		{nome: "Luis", jid: b.jid},
	}

	texto, jids, errMsg, status := applyMentions(
		"@Luis e @Luis Montes, olhem isso", []resolvedMention{a, b}, outros)

	// A primeira ancora leva o A; na segunda, o casamento mais longo
	// ("Luis Montes", do A) ja foi usado. A resposta certa e repetir o A e
	// deixar a mencao do B sem ancora — recusa — e NUNCA grifar o B onde
	// esta escrito o nome do A.
	if errMsg == "" || status != http.StatusBadRequest {
		t.Fatalf("errMsg=%q status=%d, want a 400 refusal", errMsg, status)
	}
	if jids != nil {
		t.Fatalf("jids = %v, want nil — nothing may be sent", jids)
	}
	if strings.Contains(texto, "c-b") {
		t.Fatalf("texto = %q, want no trace of the other person's number where the author wrote a full name", texto)
	}
}

// TestLeituraResolveNomePelaTabelaSenders cobre o que a verificacao do criterio
// 7 achou rodando contra as duas pontes reais: a leitura respondia
// "(contato sem nome)" para a MESMA pessoa que /api/group_info acabara de
// resolver pelo nome. Causa: getSenderName so consultava `chats`, que guarda o
// nome de uma CONVERSA; os nomes de PARTICIPANTE moram em `senders`, a tabela
// que a resolucao de mencao passou dez rodadas aprendendo a ler.
//
// As chaves sao varias porque `messages.sender` e gravado como a parte de
// usuario do JID, sem servidor, e a linha pode estar sob a forma PN ou a @lid.
func TestLeituraResolveNomePelaTabelaSenders(t *testing.T) {
	store := setupPollStore(t)
	db := store.db
	usuario := "55" + "62" + "9" + "0000008"
	pn := usuario + "@s.whatsapp.net"

	t.Run("parte_de_usuario_crua_acha_a_linha_PN", func(t *testing.T) {
		if _, err := db.Exec(
			"INSERT INTO senders (jid, push_name, full_name, first_name, business_name) VALUES (?, ?, ?, ?, ?)",
			pn, "Aninha", "Ana Paula Souza", "Ana", "",
		); err != nil {
			t.Fatalf("insert: %v", err)
		}

		resp, err := getSenderName(db, usuario, nil)
		if err != nil {
			t.Fatalf("getSenderName: %v", err)
		}
		if resp.Name != "Ana Paula Souza" {
			t.Fatalf("Name = %q, want o nome mais especifico da tabela senders", resp.Name)
		}
	})

	t.Run("linha_so_por_lid_tambem_e_lida", func(t *testing.T) {
		outro := "1122334499"
		if _, err := db.Exec(
			"INSERT INTO senders (jid, push_name, full_name, first_name, business_name) VALUES (?, ?, ?, ?, ?)",
			outro+"@lid", "", "Bruno Lima", "Bruno", "",
		); err != nil {
			t.Fatalf("insert: %v", err)
		}

		resp, err := getSenderName(db, outro, nil)
		if err != nil {
			t.Fatalf("getSenderName: %v", err)
		}
		if resp.Name != "Bruno Lima" {
			t.Fatalf("Name = %q, want o nome da linha @lid", resp.Name)
		}
	})

	t.Run("sufixo_de_dispositivo_nao_atrapalha", func(t *testing.T) {
		resp, err := getSenderName(db, usuario+":8@s.whatsapp.net", nil)
		if err != nil {
			t.Fatalf("getSenderName: %v", err)
		}
		if resp.Name != "Ana Paula Souza" {
			t.Fatalf("Name = %q", resp.Name)
		}
	})

	t.Run("nome_com_cara_de_telefone_nao_e_nome", func(t *testing.T) {
		soNumero := "55" + "62" + "9" + "00000099"
		if _, err := db.Exec(
			"INSERT INTO senders (jid, push_name, full_name, first_name, business_name) VALUES (?, ?, ?, ?, ?)",
			soNumero+"@s.whatsapp.net", soNumero, "", "", "",
		); err != nil {
			t.Fatalf("insert: %v", err)
		}

		resp, err := getSenderName(db, soNumero, nil)
		if err != nil {
			t.Fatalf("getSenderName: %v", err)
		}
		// Devolve o proprio identificador: e como quem le sabe que NAO houve
		// nome, e e ai que o marcador neutro entra do lado do servidor MCP.
		if resp.Name != soNumero {
			t.Fatalf("Name = %q, want o proprio identificador — push_name que E um telefone nao e nome", resp.Name)
		}
	})

	t.Run("chats_com_cara_de_telefone_nao_vence_a_tabela_senders", func(t *testing.T) {
		// Conversa 1:1 sem nome salvo guarda o PROPRIO NUMERO em chats.name. A
		// primeira consulta acertava ali e a busca terminava, entao a leitura
		// respondia (contato sem nome) para quem tinha nome gravado uma tabela
		// ao lado. Achado da verificacao do criterio 7 contra as pontes reais.
		u := "55" + "62" + "9" + "0000011"
		jid := u + "@s.whatsapp.net"
		if err := store.StoreChat(jid, u, time.Now()); err != nil {
			t.Fatalf("StoreChat: %v", err)
		}
		if _, err := db.Exec(
			"INSERT INTO senders (jid, push_name, full_name, first_name, business_name) VALUES (?, ?, ?, ?, ?)",
			jid, "", "Diana Rocha", "Diana", "",
		); err != nil {
			t.Fatalf("insert: %v", err)
		}

		resp, err := getSenderName(db, jid, nil)
		if err != nil {
			t.Fatalf("getSenderName: %v", err)
		}
		if resp.Name != "Diana Rocha" {
			t.Fatalf("Name = %q, want o nome da tabela senders — chats.name com cara de telefone nao e nome", resp.Name)
		}
	})

	t.Run("user_part_de_duas_pessoas_nao_escolhe_uma", func(t *testing.T) {
		// Duas linhas distintas com o MESMO user part, uma sob a forma PN e
		// outra sob a @lid: sao duas pessoas. Escolher seria imprimir o nome de
		// uma no lugar da outra, entao nao se escolhe.
		ambiguo := "7788990011"
		for _, jid := range []string{ambiguo + "@s.whatsapp.net", ambiguo + "@lid"} {
			if _, err := db.Exec(
				"INSERT INTO senders (jid, push_name, full_name, first_name, business_name) VALUES (?, ?, ?, ?, ?)",
				jid, "", "Nome De "+jid[:4], "Nome", "",
			); err != nil {
				t.Fatalf("insert: %v", err)
			}
		}

		resp, err := getSenderName(db, ambiguo, nil)
		if err != nil {
			t.Fatalf("getSenderName: %v", err)
		}
		if resp.Name != ambiguo {
			t.Fatalf("Name = %q, want o proprio identificador — dois JIDs com o mesmo user part sao duas pessoas", resp.Name)
		}
	})

	t.Run("com_o_mapa_da_lib_a_outra_forma_e_alcancada", func(t *testing.T) {
		// Endereco completo cuja linha esta sob a OUTRA forma. Quem sabe a
		// traducao e o mapa da lib, nunca uma troca de servidor na mao.
		pnOutro := types.JID{User: "55" + "62" + "9" + "0000010", Server: types.DefaultUserServer}
		lidOutro := types.JID{User: "6655443322", Server: types.HiddenUserServer}
		if _, err := db.Exec(
			"INSERT INTO senders (jid, push_name, full_name, first_name, business_name) VALUES (?, ?, ?, ?, ?)",
			lidOutro.String(), "", "Carla Mendes", "Carla", "",
		); err != nil {
			t.Fatalf("insert: %v", err)
		}

		semMapa, err := getSenderName(db, pnOutro.String(), nil)
		if err != nil {
			t.Fatalf("getSenderName: %v", err)
		}
		if semMapa.Name == "Carla Mendes" {
			t.Fatalf("sem mapa nao havia como traduzir, e veio %q", semMapa.Name)
		}

		comMapa, err := getSenderName(db, pnOutro.String(), mapaFixo{pn: pnOutro, lid: lidOutro})
		if err != nil {
			t.Fatalf("getSenderName: %v", err)
		}
		if comMapa.Name != "Carla Mendes" {
			t.Fatalf("Name = %q, want o nome da linha sob a outra forma", comMapa.Name)
		}
	})

	t.Run("sem_linha_nenhuma_devolve_o_proprio_identificador", func(t *testing.T) {
		desconhecido := "55" + "62" + "9" + "00000077"
		resp, err := getSenderName(db, desconhecido, nil)
		if err != nil {
			t.Fatalf("getSenderName: %v", err)
		}
		if resp.Name != desconhecido {
			t.Fatalf("Name = %q", resp.Name)
		}
	})
}

// TestListaDeParticipantesDeCadaLado cobre os achados 1 e 2 da rodada 10. O
// achado 2 e um buraco de bateria: reverter a correcao da rodada 9 dentro do
// laco de chatParticipants — voltar a descartar quem nao tem telefone —
// deixava a suite INTEIRA verde, porque nenhum teste chamava a funcao. O
// achado 1 e o outro lado dela: o ramo 1:1 montava o participante sem
// outrasChaves, entao a correcao do achado 3 da rodada 9 valia so em grupo e a
// linha @lid nunca era lida numa conversa particular.
func TestListaDeParticipantesDeCadaLado(t *testing.T) {
	dir := t.TempDir()
	db, err := sql.Open("sqlite3", filepath.Join(dir, "messages.db"))
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer db.Close()
	if _, err := db.Exec(`CREATE TABLE senders (
		jid TEXT PRIMARY KEY, push_name TEXT, full_name TEXT, first_name TEXT, business_name TEXT
	)`); err != nil {
		t.Fatalf("create: %v", err)
	}
	store := &MessageStore{db: db}

	t.Run("grupo_mantem_quem_nao_tem_telefone", func(t *testing.T) {
		comTelefone := types.JID{User: "55" + "62" + "9" + "0000005", Server: types.DefaultUserServer}
		semTelefone := types.JID{User: "9988776655", Server: types.HiddenUserServer}
		info := &types.GroupInfo{Participants: []types.GroupParticipant{
			{JID: comTelefone, PhoneNumber: comTelefone},
			{JID: semTelefone, LID: semTelefone},
		}}

		participants := participantesDeGrupo(store, info)

		if len(participants) != 2 {
			t.Fatalf("participants = %v — quem nao tem telefone continua na lista: o nome dele protege o prefixo e conta na D6", participants)
		}
		if participants[1].phoneUser != "" {
			t.Fatalf("phoneUser = %q, want vazio", participants[1].phoneUser)
		}
	})

	t.Run("conversa_1a1_le_a_linha_lid", func(t *testing.T) {
		lid := types.JID{User: "1122334455", Server: types.HiddenUserServer}
		if _, err := db.Exec(
			"INSERT INTO senders (jid, push_name, full_name, first_name, business_name) VALUES (?, ?, ?, ?, ?)",
			lid.String(), "", "Ana Paula Souza", "Ana Paula", "",
		); err != nil {
			t.Fatalf("insert: %v", err)
		}
		chat := types.JID{User: "55" + "62" + "9" + "0000006", Server: types.DefaultUserServer}

		participants := participantesDaConversa(store, chat, mapaFixo{pn: chat, lid: lid})

		if len(participants) != 1 {
			t.Fatalf("participants = %v", participants)
		}
		if participants[0].fullName != "Ana Paula Souza" {
			t.Fatalf("fullName = %q — em 1:1 a linha @lid tambem tem de ser lida; sem ela a mesma pessoa e mencionavel no grupo e recusada na conversa", participants[0].fullName)
		}
		if len(matchMentionName(participants, "Ana Paula")) != 1 {
			t.Fatalf("want a pessoa mencionavel em 1:1 pelo nome que so existe na linha @lid")
		}
	})

	t.Run("sem_mapa_de_lid_nao_estoura", func(t *testing.T) {
		chat := types.JID{User: "55" + "62" + "9" + "0000009", Server: types.DefaultUserServer}
		if participants := participantesDaConversa(store, chat, nil); len(participants) != 1 {
			t.Fatalf("participants = %v — contato novo nao tem mapeamento, e isso e o caso comum", participants)
		}
	})
}

// mapaFixo e um duble do mapa LID<->PN da lib, com um unico par.
type mapaFixo struct {
	pn  types.JID
	lid types.JID
}

func (m mapaFixo) GetLIDForPN(_ context.Context, pn types.JID) (types.JID, error) {
	if pn.String() == m.pn.String() {
		return m.lid, nil
	}
	return types.JID{}, nil
}

func (m mapaFixo) GetPNForLID(_ context.Context, lid types.JID) (types.JID, error) {
	if lid.String() == m.lid.String() {
		return m.pn, nil
	}
	return types.JID{}, nil
}

// TestCitacaoApontaOAutorPeloJIDInteiro e a guarda que faltava (achado 3 da
// rodada 10): trocar o Participant do ContextInfo pelo user part cru, sem
// "@servidor", deixava a bateria verde. Os testes de citacao cobriam as tres
// recusas e nunca o caminho de sucesso, e a unica assercao sobre Participant na
// suite era sobre um ContextInfo montado pelo proprio teste — caminho de
// leitura, nao de escrita.
func TestCitacaoApontaOAutorPeloJIDInteiro(t *testing.T) {
	store := setupPollStore(t)
	const grupo = "grupo-da-guarda-de-citacao@g.us"
	autor := "55" + "62" + "9" + "0000007" + "@s.whatsapp.net"
	if err := store.StoreChat(grupo, "Grupo", time.Now()); err != nil {
		t.Fatalf("StoreChat: %v", err)
	}
	if err := store.StoreMessage("MSG-CITADA", grupo, "alguem", "bom dia a todos", time.Now(), false, "", "", "", nil, nil, nil, 0); err != nil {
		t.Fatalf("StoreMessage: %v", err)
	}
	if err := store.StoreMessageSenderJID("MSG-CITADA", grupo, autor); err != nil {
		t.Fatalf("StoreMessageSenderJID: %v", err)
	}

	ctxInfo, errMsg, status := buildQuoteContextInfo(store, "MSG-CITADA", grupo)

	if errMsg != "" || status != 0 || ctxInfo == nil {
		t.Fatalf("errMsg=%q status=%d ctxInfo=%v", errMsg, status, ctxInfo)
	}
	if ctxInfo.GetStanzaID() != "MSG-CITADA" {
		t.Fatalf("StanzaID = %q", ctxInfo.GetStanzaID())
	}
	// O ponto do teste: Participant tem de ser o JID INTEIRO do autor. Sem o
	// "@servidor" o destinatario nao tem a quem atribuir a citacao.
	if ctxInfo.GetParticipant() != autor {
		t.Fatalf("Participant = %q, want o JID inteiro do autor", ctxInfo.GetParticipant())
	}
	if ctxInfo.GetQuotedMessage().GetConversation() != "bom dia a todos" {
		t.Fatalf("QuotedMessage = %q", ctxInfo.GetQuotedMessage().GetConversation())
	}
}

// TestRecusaDizQuemComeuAAncora cobre o achado 4 da rodada 10: com a
// generosidade de separador, o nome de um terceiro passa a vencer a posicao
// e a mencao pedida fica sem ancora — recusa, que e falha segura. Mas o texto
// da recusa dizia que "@Ana" nao esta na mensagem, com "@Ana" na mensagem.
func TestRecusaDizQuemComeuAAncora(t *testing.T) {
	participants := []mentionParticipant{
		{jid: "ana@s.whatsapp.net", phoneUser: "ana", firstName: "Ana"},
		{jid: "ap@s.whatsapp.net", phoneUser: "ap", fullName: "Ana Paula"},
	}
	resolved, outros, _, errMsg, _ := resolveMentionsAgainstParticipants(
		participants, []string{"Ana"}, "g@g.us")
	if errMsg != "" {
		t.Fatalf("resolucao: %v", errMsg)
	}

	_, _, ancora, status := applyMentions("@Ana - Paula ja respondeu", resolved, outros)

	if status != http.StatusBadRequest {
		t.Fatalf("status = %d, want recusa", status)
	}
	if !strings.Contains(ancora, "Ana Paula") {
		t.Fatalf("a recusa %q nao diz quem venceu a posicao — quem le nao tem como entender por que", ancora)
	}
	if strings.Contains(ancora, "has no") {
		t.Fatalf("a recusa %q ainda diz que a ancora nao esta no texto, e ela esta", ancora)
	}
}

// TestGrupoDescritoPorNome cobre a decisao do Luis na rodada 9: /api/group_info
// devolvia jid, phone_number e lid de TODO participante — num grupo de 40
// pessoas, 40 telefones numa resposta de API, contra a D3. Passa a devolver o
// nome e um ref opaco.
func TestGrupoDescritoPorNome(t *testing.T) {
	dir := t.TempDir()
	db, err := sql.Open("sqlite3", filepath.Join(dir, "messages.db"))
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer db.Close()
	if _, err := db.Exec(`CREATE TABLE senders (
		jid TEXT PRIMARY KEY, push_name TEXT, full_name TEXT, first_name TEXT, business_name TEXT
	)`); err != nil {
		t.Fatalf("create: %v", err)
	}
	const grupo = "grupo-de-teste@g.us"
	// Numeros montados em tempo de execucao: repo publico, e a trava de dado
	// pessoal recusa a forma escrita por extenso.
	ddi := "55" + "62"
	umaPessoa := types.JID{User: ddi + "9" + "0000001", Server: types.DefaultUserServer}
	outra := types.JID{User: ddi + "9" + "0000002", Server: types.DefaultUserServer}
	semNome := types.JID{User: ddi + "9" + "0000003", Server: types.DefaultUserServer}
	if _, err := db.Exec(
		"INSERT INTO senders (jid, push_name, full_name, first_name, business_name) VALUES (?, ?, ?, ?, ?)",
		umaPessoa.String(), "", "Ana Paula", "Ana", "",
	); err != nil {
		t.Fatalf("insert: %v", err)
	}
	if _, err := db.Exec(
		"INSERT INTO senders (jid, push_name, full_name, first_name, business_name) VALUES (?, ?, ?, ?, ?)",
		outra.String(), "", "Ana Paula", "Ana", "",
	); err != nil {
		t.Fatalf("insert: %v", err)
	}
	store := &MessageStore{db: db}
	info := &types.GroupInfo{Participants: []types.GroupParticipant{
		{JID: umaPessoa, PhoneNumber: umaPessoa, IsAdmin: true},
		{JID: outra, PhoneNumber: outra},
		{JID: semNome, PhoneNumber: semNome},
	}}

	saida := participantesPorNome(store, info, grupo)

	if len(saida) != 3 {
		t.Fatalf("saida = %v", saida)
	}
	bruto, err := json.Marshal(saida)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if corrida := regexp.MustCompile(`[0-9]{8,}`).FindString(string(bruto)); corrida != "" {
		t.Fatalf("a resposta carrega uma corrida de %d digitos: %s", len(corrida), bruto)
	}
	for _, campo := range []string{"phone_number", "lid", "jid"} {
		if strings.Contains(string(bruto), `"`+campo+`"`) {
			t.Fatalf("a resposta ainda tem o campo %q: %s", campo, bruto)
		}
	}
	if saida[0]["name"] == saida[1]["name"] {
		t.Fatalf("dois membros com o rotulo %v — nao da para apontar um deles", saida[0]["name"])
	}
	if saida[2]["name"] != contatoSemNome {
		t.Fatalf("sem nome no store, saiu %v — o numero nao pode preencher a lacuna", saida[2]["name"])
	}
	if saida[0]["is_admin"] != true {
		t.Fatalf("is_admin perdido: %v", saida[0])
	}
}

// TestRefDeGrupoApontaAPessoa: o ref que /api/group_info devolve e o unico jeito
// de agir sobre um membro sem o numero dele passar pela resposta, entao
// /api/group_participants tem de aceita-lo — e so no grupo que o emitiu.
func TestRefDeGrupoApontaAPessoa(t *testing.T) {
	const grupo = "grupo-de-teste@g.us"
	alvo := "55" + "62" + "9" + "0000004" + "@s.whatsapp.net"
	ref := storeMentionRef(alvo, "Ana Paula", grupo)

	t.Run("no_grupo_que_o_emitiu_resolve", func(t *testing.T) {
		jids, err := parseGroupParticipantJIDs([]string{"ref:" + ref}, grupo)
		if err != nil {
			t.Fatalf("erro: %v", err)
		}
		if len(jids) != 1 || jids[0].String() != alvo {
			t.Fatalf("jids = %v", jids)
		}
	})

	t.Run("em_outro_grupo_recusa", func(t *testing.T) {
		_, err := parseGroupParticipantJIDs([]string{"ref:" + ref}, "outro-grupo@g.us")
		if err == nil {
			t.Fatalf("um ref de outro grupo resolveu — ele tem de ser preso a quem o emitiu")
		}
		if len(digitosDe(err.Error())) >= 8 {
			t.Fatalf("a recusa %q carrega numero", err)
		}
	})

	t.Run("ref_desconhecido_recusa", func(t *testing.T) {
		if _, err := parseGroupParticipantJIDs([]string{"ref:nao-existe"}, grupo); err == nil {
			t.Fatalf("ref desconhecido foi aceito")
		}
	})
}

// TestSeparadorNaoAbreOPrefixo cobre o achado 1 da rodada 9. A protecao de
// prefixo so vale se o nome longo do outro participante CASAR no texto, e a
// regua da rodada 8 dobrava caixa e acento — e mais nada. Bastava o autor
// separar as duas partes do nome de um jeito diferente do que a agenda guarda
// (espaco duplo, espaco inquebravel de teclado de celular, quebra de linha,
// hifen) para o nome longo nao casar, o curto vencer a posicao e a mensagem
// sair ENVIADA com a pessoa errada grifada.
func TestSeparadorNaoAbreOPrefixo(t *testing.T) {
	participants := []mentionParticipant{
		{jid: "ana@s.whatsapp.net", phoneUser: "ana", firstName: "Ana"},
		{jid: "ap@s.whatsapp.net", phoneUser: "ap", fullName: "Ana Paula"},
	}

	for _, caso := range []struct {
		nome  string
		texto string
	}{
		{"espaco_simples", "bom dia @Ana Paula"},
		{"espaco_duplo", "bom dia @Ana  Paula"},
		{"espaco_inquebravel", "bom dia @Ana\u00a0Paula"},
		{"espaco_fino_inquebravel", "bom dia @Ana\u202fPaula"},
		{"quebra_de_linha", "bom dia @Ana\nPaula"},
		{"hifen", "bom dia @Ana-Paula"},
	} {
		t.Run(caso.nome, func(t *testing.T) {
			resolved, outros, _, errMsg, _ := resolveMentionsAgainstParticipants(
				participants, []string{"Ana"}, "g@g.us")
			if errMsg != "" {
				t.Fatalf("resolucao: %v", errMsg)
			}

			texto, jids, ancora, status := applyMentions(caso.texto, resolved, outros)

			if ancora == "" || status != http.StatusBadRequest {
				t.Fatalf("ENVIADO: %q -> %q (jids=%v) — a Ana grifada onde o autor nomeou a Ana Paula",
					caso.texto, texto, jids)
			}
			if texto != caso.texto {
				t.Fatalf("texto = %q — nada pode ser reescrito numa recusa", texto)
			}
		})
	}

	t.Run("separador_diferente_na_propria_ancora_ainda_grifa", func(t *testing.T) {
		// O outro lado da mesma regua: quem pede "Ana Paula" e escreve
		// "@Ana  Paula" pediu a Ana Paula.
		resolved, outros, _, errMsg, _ := resolveMentionsAgainstParticipants(
			participants, []string{"Ana Paula"}, "g@g.us")
		if errMsg != "" {
			t.Fatalf("resolucao: %v", errMsg)
		}

		texto, jids, ancora, status := applyMentions("bom dia @Ana  Paula", resolved, outros)

		if ancora != "" || status != 0 {
			t.Fatalf("ancora=%q status=%d texto=%q, want envio", ancora, status, texto)
		}
		if texto != "bom dia @ap" {
			t.Fatalf("texto = %q", texto)
		}
		if len(jids) != 1 || jids[0] != "ap@s.whatsapp.net" {
			t.Fatalf("jids = %v", jids)
		}
	})
}

// TestParticipanteSemTelefoneContinuaContando cobre o achado 2 da rodada 9.
// Quem o grupo devolve sem telefone nao pode ser mencionado — mas some da lista
// era errado duas vezes: o nome dele ainda precisa proteger o prefixo de um
// nome mais curto, e ainda precisa contar na ambiguidade da D6. Sumindo, as
// duas viravam mencao silenciosa da pessoa errada.
func TestParticipanteSemTelefoneContinuaContando(t *testing.T) {
	t.Run("o_nome_dele_ainda_protege_o_prefixo", func(t *testing.T) {
		participants := []mentionParticipant{
			{jid: "ana@s.whatsapp.net", phoneUser: "ana", firstName: "Ana"},
			{jid: "so-lid@lid", fullName: "Ana Paula"}, // sem phoneUser
		}
		resolved, outros, _, errMsg, _ := resolveMentionsAgainstParticipants(
			participants, []string{"Ana"}, "g@g.us")
		if errMsg != "" {
			t.Fatalf("resolucao: %v", errMsg)
		}

		texto, _, ancora, status := applyMentions("@Ana Paula, cuidado com o prazo", resolved, outros)

		if ancora == "" || status != http.StatusBadRequest {
			t.Fatalf("ENVIADO: texto = %q — a Ana grifada onde o autor nomeou a Ana Paula", texto)
		}
	})

	t.Run("ele_ainda_conta_na_ambiguidade_da_D6", func(t *testing.T) {
		participants := []mentionParticipant{
			{jid: "ana@s.whatsapp.net", phoneUser: "ana", firstName: "Ana", pushName: "Ana da Loja"},
			{jid: "so-lid@lid", firstName: "Ana", pushName: "Ana do Predio"}, // sem phoneUser
		}

		resolved, _, candidates, errMsg, status := resolveMentionsAgainstParticipants(
			participants, []string{"Ana"}, "g@g.us")

		if len(candidates) != 2 || errMsg == "" || status != http.StatusBadRequest {
			t.Fatalf("resolved=%v candidates=%v errMsg=%q — a D6 tem de perguntar, nao escolher",
				resolved, candidates, errMsg)
		}
	})

	t.Run("mencionar_so_ele_recusa_sem_dizer_numero", func(t *testing.T) {
		participants := []mentionParticipant{
			{jid: "so-lid@lid", firstName: "Ana"},
		}

		resolved, _, _, errMsg, status := resolveMentionsAgainstParticipants(
			participants, []string{"Ana"}, "g@g.us")

		if errMsg == "" || status != http.StatusBadRequest || resolved != nil {
			t.Fatalf("resolved=%v errMsg=%q status=%d, want recusa", resolved, errMsg, status)
		}
		if strings.Contains(errMsg, "lid") || len(digitosDe(errMsg)) >= 8 {
			t.Fatalf("a recusa %q carrega endereco — a D3 nao admite", errMsg)
		}
	})
}

// TestParticipanteDoGrupoGuardaOLID cobre o achado 3 da rodada 9. A chave
// alternativa vinha de gp.JID, que a documentacao do whatsmeow define como
// "always equals either the LID or phone number" — ou seja, a forma PN sempre
// que o grupo e enderecado por telefone. A busca consultava a MESMA linha duas
// vezes, e a linha @lid (587 dos 2.518 nomes no store real) nunca era lida.
//
// O teste passa pela conversao que a producao usa, nao por um participante
// montado a mao: montar a mao prova que a busca le o campo, nao que a producao
// o preenche.
func TestParticipanteDoGrupoGuardaOLID(t *testing.T) {
	pn := types.JID{User: "participante-b", Server: types.DefaultUserServer}
	lid := types.JID{User: "9988776655", Server: types.HiddenUserServer}

	t.Run("grupo_enderecado_por_telefone_ainda_guarda_o_lid", func(t *testing.T) {
		// gp.JID == gp.PhoneNumber: exatamente o caso em que a chave antiga
		// virava copia do jid.
		p := participanteDoGrupo(types.GroupParticipant{JID: pn, PhoneNumber: pn, LID: lid})

		if p.jid != pn.String() || p.phoneUser != pn.User {
			t.Fatalf("jid=%q phoneUser=%q", p.jid, p.phoneUser)
		}
		achou := false
		for _, c := range p.outrasChaves {
			if c == lid.String() {
				achou = true
			}
			if c == p.jid {
				t.Fatalf("outrasChaves repete o proprio jid: %v", p.outrasChaves)
			}
		}
		if !achou {
			t.Fatalf("outrasChaves = %v, want o LID entre elas", p.outrasChaves)
		}
	})

	t.Run("sem_telefone_entra_na_lista_sem_poder_ser_mencionado", func(t *testing.T) {
		p := participanteDoGrupo(types.GroupParticipant{JID: lid, LID: lid})

		if p.jid != lid.String() {
			t.Fatalf("jid = %q, want a pessoa presente mesmo sem telefone", p.jid)
		}
		if p.phoneUser != "" {
			t.Fatalf("phoneUser = %q, want vazio — nao ha numero com que menciona-la", p.phoneUser)
		}
	})
}

// TestPerguntaDaD6ESempreRespondivel: a D6 para de enviar justamente para
// perguntar "qual dos dois?". Dois candidatos com o MESMO rotulo devolvem o
// usuario ao escuro — a rodada 7 tratou o caso do campo que casou, e sobrou o
// caso de dois homonimos de nome completo com push_name diferente.
func TestPerguntaDaD6ESempreRespondivel(t *testing.T) {
	t.Run("homonimos_de_nome_completo_se_separam_pelo_outro_nome", func(t *testing.T) {
		participants := []mentionParticipant{
			{jid: "a@s.whatsapp.net", fullName: "Ana Paula", pushName: "Aninha"},
			{jid: "b@s.whatsapp.net", fullName: "Ana Paula", pushName: "Paula do Mercado"},
		}

		_, _, candidates, errMsg, status := resolveMentionsAgainstParticipants(
			participants, []string{"Ana Paula"}, "g@g.us")

		if errMsg == "" || status != http.StatusBadRequest || len(candidates) != 2 {
			t.Fatalf("errMsg=%q status=%d candidates=%v, want a pergunta da D6", errMsg, status, candidates)
		}
		if candidates[0].Nome == candidates[1].Nome {
			t.Fatalf("os dois candidatos saem como %q — impossivel escolher", candidates[0].Nome)
		}
		for _, c := range candidates {
			if !strings.Contains(c.Nome, "Ana Paula") {
				t.Fatalf("candidato %q perdeu o nome que o usuario escreveu", c.Nome)
			}
			if len(digitosDe(c.Nome)) >= 8 {
				t.Fatalf("candidato %q carrega numero — a D3 nao admite", c.Nome)
			}
		}
	})

	t.Run("sem_nenhum_nome_que_separe_entra_a_ordem", func(t *testing.T) {
		participants := []mentionParticipant{
			{jid: "a@s.whatsapp.net", fullName: "Ana Paula"},
			{jid: "b@s.whatsapp.net", fullName: "Ana Paula"},
		}

		_, _, candidates, _, _ := resolveMentionsAgainstParticipants(
			participants, []string{"Ana Paula"}, "g@g.us")

		if len(candidates) != 2 || candidates[0].Nome == candidates[1].Nome {
			t.Fatalf("candidates = %v — mesmo sem nome que separe, tem de dar para apontar um", candidates)
		}
	})
}

// TestMesmaReguaDeNomeNosDoisLados cobre o bloqueante da rodada 8: a resolucao
// casava nome normalizado (matchMentionName -> stripAccents, que faz NFD e
// ToLower) e a varredura do texto casava byte a byte. Com isso o candidato
// longo que existe SO para proteger o prefixo — o nome do outro participante —
// deixava de casar quando a agenda guardava caixa ou acento diferentes do que
// o autor escreveu, e saia o numero do primeiro onde o autor escreveu o nome do
// segundo. Sem recusa: mensagem enviada, pessoa errada grifada.
func TestMesmaReguaDeNomeNosDoisLados(t *testing.T) {
	t.Run("caixa_diferente_na_agenda_ainda_protege_o_prefixo", func(t *testing.T) {
		participants := []mentionParticipant{
			{jid: "ana@s.whatsapp.net", phoneUser: "ana-user", firstName: "Ana"},
			// Contato importado em CAIXA ALTA, como e comum vir da agenda.
			{jid: "ap@s.whatsapp.net", phoneUser: "ap-user", fullName: "ANA PAULA"},
		}
		resolved, outros, _, errMsg, _ := resolveMentionsAgainstParticipants(
			participants, []string{"Ana"}, "g@g.us")
		if errMsg != "" {
			t.Fatalf("resolucao: %v", errMsg)
		}

		texto, jids, ancora, status := applyMentions("bom dia @Ana Paula", resolved, outros)

		if ancora == "" || status != http.StatusBadRequest {
			t.Fatalf("ancora=%q status=%d texto=%q jids=%v, want recusa 400 — falha segura",
				ancora, status, texto, jids)
		}
		if texto != "bom dia @Ana Paula" {
			t.Fatalf("texto = %q — nada pode ser reescrito numa recusa", texto)
		}
	})

	t.Run("acento_so_na_agenda_ainda_protege_o_prefixo", func(t *testing.T) {
		participants := []mentionParticipant{
			{jid: "jose@s.whatsapp.net", phoneUser: "jose-user", firstName: "Jose"},
			{jid: "ja@s.whatsapp.net", phoneUser: "ja-user", fullName: "José Antônio"},
		}
		resolved, outros, _, errMsg, _ := resolveMentionsAgainstParticipants(
			participants, []string{"Jose"}, "g@g.us")
		if errMsg != "" {
			t.Fatalf("resolucao: %v", errMsg)
		}

		texto, _, ancora, status := applyMentions("bom dia @Jose Antonio", resolved, outros)

		if ancora == "" || status != http.StatusBadRequest {
			t.Fatalf("ancora=%q status=%d texto=%q, want recusa 400", ancora, status, texto)
		}
		if texto != "bom dia @Jose Antonio" {
			t.Fatalf("texto = %q — numero do Jose onde o autor escreveu Jose Antonio", texto)
		}
	})

	t.Run("ancora_em_caixa_diferente_do_pedido_ainda_grifa", func(t *testing.T) {
		// O outro lado da mesma regua: quem escreve "@ANA" pediu a Ana. Antes
		// da rodada 8 isso nao casava e a mencao morria numa recusa.
		participants := []mentionParticipant{
			{jid: "ana@s.whatsapp.net", phoneUser: "ana-user", firstName: "Ana"},
		}
		resolved, outros, _, errMsg, _ := resolveMentionsAgainstParticipants(
			participants, []string{"Ana"}, "g@g.us")
		if errMsg != "" {
			t.Fatalf("resolucao: %v", errMsg)
		}

		texto, jids, ancora, status := applyMentions("oi @ANA, tudo bem?", resolved, outros)

		if ancora != "" || status != 0 {
			t.Fatalf("ancora=%q status=%d, want envio", ancora, status)
		}
		if texto != "oi @ana-user, tudo bem?" {
			t.Fatalf("texto = %q", texto)
		}
		if len(jids) != 1 || jids[0] != "ana@s.whatsapp.net" {
			t.Fatalf("jids = %v", jids)
		}
	})

	t.Run("nome_intacto_sai_com_a_grafia_do_autor", func(t *testing.T) {
		// O candidato que vence sem ter sido pedido fica como esta (D5) — e
		// "como esta" e o texto de QUEM ESCREVEU, nao o nome da agenda. Como o
		// casamento agora e normalizado, os dois podem diferir, e reescrever a
		// grafia do autor seria mexer no texto dele sem ter sido pedido.
		participants := []mentionParticipant{
			{jid: "ana@s.whatsapp.net", phoneUser: "ana-user", firstName: "Ana"},
			{jid: "ap@s.whatsapp.net", phoneUser: "ap-user", fullName: "ANA PAULA"},
		}
		resolved, outros, _, errMsg, _ := resolveMentionsAgainstParticipants(
			participants, []string{"Ana"}, "g@g.us")
		if errMsg != "" {
			t.Fatalf("resolucao: %v", errMsg)
		}

		texto, _, ancora, status := applyMentions("@Ana Paula e @Ana, vejam", resolved, outros)

		if ancora != "" || status != 0 {
			t.Fatalf("ancora=%q status=%d, want envio", ancora, status)
		}
		if texto != "@Ana Paula e @ana-user, vejam" {
			t.Fatalf("texto = %q — a grafia do autor tem de sobreviver intacta", texto)
		}
	})

	t.Run("nome_compartilhado_em_caixa_diferente_conta_como_compartilhado", func(t *testing.T) {
		// A contagem de donos de um nome tambem e uma comparacao de nome, e
		// tambem precisa da mesma regua: com "Ana Paula" na agenda de um e
		// "ANA PAULA" no push_name do outro, o nome E compartilhado, e ligar
		// mesmo assim escolheria por conta propria qual dos dois o autor quis
		// — o dano que a D6 existe para impedir.
		participants := []mentionParticipant{
			{jid: "a@s.whatsapp.net", phoneUser: "a-user", fullName: "Ana Paula Souza", firstName: "Ana Paula"},
			{jid: "b@s.whatsapp.net", phoneUser: "b-user", pushName: "ANA PAULA"},
		}
		resolved, outros, _, errMsg, _ := resolveMentionsAgainstParticipants(
			participants, []string{"Ana Paula Souza"}, "g@g.us")
		if errMsg != "" {
			t.Fatalf("resolucao: %v", errMsg)
		}

		texto, _, ancora, status := applyMentions("oi @Ana Paula", resolved, outros)

		if ancora == "" || status != http.StatusBadRequest {
			t.Fatalf("ancora=%q status=%d, want recusa 400", ancora, status)
		}
		if texto != "oi @Ana Paula" {
			t.Fatalf("texto = %q — nome compartilhado nao pode ser ligado sem desambiguacao", texto)
		}
	})
}

// TestTextoNormalizadoTraduzPosicao guarda o contrato do que a rodada 8
// introduziu: normalizar muda o comprimento em bytes, entao o casamento so
// pode andar no texto normalizado se souber devolver quantos bytes do texto
// ORIGINAL foram consumidos. Se as duas traducoes de posicao sairem de fase, o
// recorte pega metade de um rune e o texto enviado sai corrompido.
func TestTextoNormalizadoTraduzPosicao(t *testing.T) {
	for _, entrada := range []string{
		"", "@Ana Paula", "José Antônio", "ÁÉÍÓÚ ç", "@ANA, tudo bem?",
		"emoji 🙂 e acento à toa", "combinando A\u0301 solto",
	} {
		normalizado, paraOriginal, paraNormalizado := textoNormalizado(entrada)

		if normalizado != stripAccents(entrada) {
			t.Fatalf("%q: normalizado = %q, want %q — tem de ser a MESMA regua de matchMentionName",
				entrada, normalizado, stripAccents(entrada))
		}
		if len(paraOriginal) != len(normalizado)+1 {
			t.Fatalf("%q: paraOriginal tem %d entradas, want %d", entrada, len(paraOriginal), len(normalizado)+1)
		}
		if paraOriginal[len(normalizado)] != len(entrada) {
			t.Fatalf("%q: fim do normalizado aponta para %d, want %d", entrada, paraOriginal[len(normalizado)], len(entrada))
		}
		for np := 1; np < len(paraOriginal); np++ {
			if paraOriginal[np] < paraOriginal[np-1] {
				t.Fatalf("%q: paraOriginal anda para tras em %d", entrada, np)
			}
			if o := paraOriginal[np]; o < len(entrada) && !utf8.RuneStart(entrada[o]) {
				t.Fatalf("%q: paraOriginal[%d] = %d cai no meio de um rune — o recorte sairia corrompido",
					entrada, np, o)
			}
		}
		for o, r := range entrada {
			np := paraNormalizado[o]
			if np < 0 || np > len(normalizado) {
				t.Fatalf("%q: posicao %d cai fora do normalizado (%d)", entrada, o, np)
			}
			// Ida e volta exata para o rune que sobrevive a normalizacao; o
			// que se apaga (a marca de acento) aponta para o proximo, que e o
			// que faz "@Ana" seguido de acento solto consumir o acento junto.
			if stripAccents(string(r)) == "" {
				continue
			}
			if volta := paraOriginal[np]; volta != o {
				t.Fatalf("%q: posicao %d volta de %d como %d — traducoes fora de fase", entrada, o, np, volta)
			}
		}
	}
}

// TestMencaoPorNumeroRecusaExplicitamente cobre a observacao 5 da rodada 6: a
// defesa contra mencionar por numero era acidental ("numero nao casa nome").
// Basta o push_name de alguem SER o proprio telefone — medido: 1 em 2.547
// remetentes do store real — para a borda abrir.
func TestMencaoPorNumeroRecusaExplicitamente(t *testing.T) {
	const comCaraDeTelefone = "5562" + "000000" + "55"
	participants := []mentionParticipant{
		{jid: "contato-a@s.whatsapp.net", phoneUser: "contato-a", pushName: comCaraDeTelefone},
	}
	if m := matchMentionName(participants, comCaraDeTelefone); m != nil {
		t.Fatalf("matches = %v, want none — a mention is by name, never by number", m)
	}
	_, _, _, errMsg, status := resolveMentionsAgainstParticipants(participants, []string{comCaraDeTelefone}, chatDeTeste)
	if errMsg == "" || status != http.StatusBadRequest {
		t.Fatalf("errMsg=%q status=%d, want a 400 refusal", errMsg, status)
	}
}

// TestResolveDevolveNomesDoChat cobre a fiacao entre resolveMentions e
// applyMentions: a varredura so consegue deixar "@Ana Paula" intacto se
// receber os nomes de QUEM MAIS esta no chat, e quem os conhece e a resolucao.
// Sem esta bateria, o defeito da rodada 3 volta apagando um `return` — o teste
// de applyMentions passa a lista na mao e nao ve a fiacao sumir.
func TestResolveDevolveNomesDoChat(t *testing.T) {
	participants := []mentionParticipant{
		{jid: "contato-ana@s.whatsapp.net", phoneUser: "contato-ana", pushName: "Ana"},
		{jid: "contato-ap@s.whatsapp.net", phoneUser: "contato-ap", fullName: "Ana Paula"},
	}
	resolved, outrosNomes, candidates, errMsg, _ := resolveMentionsAgainstParticipants(participants, []string{"Ana"}, chatDeTeste)
	if errMsg != "" || candidates != nil || len(resolved) != 1 {
		t.Fatalf("errMsg=%q candidates=%v resolved=%d, want a single clean match", errMsg, candidates, len(resolved))
	}
	achou := false
	for _, n := range outrosNomes {
		if n.nome == "Ana Paula" {
			achou = true
		}
	}
	if !achou {
		t.Fatalf("outrosNomes = %v, want it to carry the other participant's longer name", outrosNomes)
	}

	texto, jids, ancoraErr, _ := applyMentions("bom dia @Ana e @Ana Paula", resolved, outrosNomes)
	if ancoraErr != "" {
		t.Fatalf("unexpected refusal: %v", ancoraErr)
	}
	esperado := "bom dia @contato-ana e @Ana Paula"
	if texto != esperado {
		t.Fatalf("texto = %q, want %q", texto, esperado)
	}
	if len(jids) != 1 {
		t.Fatalf("jids = %v, want only the requested mention", jids)
	}
}

// TestApplyMentionsPrefixo cobre o bloqueante 2 da revisao de 2026-09-09: o
// laco de strings.ReplaceAll por nome, na ordem em que o chamador listou,
// deixava o nome curto comer o prefixo do longo. Com "Ana" e "Ana Paula" no
// mesmo texto, "@Ana Paula" virava "@<numero da Ana> Paula": a Ana grifada
// onde o autor escreveu Ana Paula, e a Ana Paula com MentionedJID sem ancora
// nenhuma no corpo. Mencionar a pessoa errada em grupo nao tem desfazer.
func TestApplyMentionsPrefixo(t *testing.T) {
	curta := resolvedMention{name: "Ana", phoneUser: "ana-user", jid: "ana@s.whatsapp.net"}
	longa := resolvedMention{name: "Ana Paula", phoneUser: "anapaula-user", jid: "anapaula@s.whatsapp.net"}

	t.Run("nome_curto_nao_come_o_prefixo_do_longo", func(t *testing.T) {
		// Ordem do chamador de proposito: a curta PRIMEIRO, que e exatamente a
		// ordem em que o defeito aparecia.
		texto, jids, errMsg, _ := applyMentions(
			"@Ana e @Ana Paula, vejam isso",
			[]resolvedMention{curta, longa},
			nil,
		)
		if errMsg != "" {
			t.Fatalf("unexpected refusal: %v", errMsg)
		}
		esperado := "@ana-user e @anapaula-user, vejam isso"
		if texto != esperado {
			t.Fatalf("texto = %q, want %q", texto, esperado)
		}
		if len(jids) != 2 {
			t.Fatalf("jids = %v, want the two mentions", jids)
		}
	})

	t.Run("numero_substituido_nao_e_relido_como_nome", func(t *testing.T) {
		// Um nome que e prefixo do numero substituido de outro nao pode ser
		// reencontrado na segunda passada: a varredura e uma so, da esquerda
		// para a direita, e nunca rele o que ja escreveu.
		texto, _, errMsg, _ := applyMentions(
			"@Ana Paula bom dia",
			[]resolvedMention{longa},
			nil,
		)
		if errMsg != "" {
			t.Fatalf("unexpected refusal: %v", errMsg)
		}
		if strings.Contains(texto, "@ana-user") {
			t.Fatalf("texto = %q, want no trace of the shorter substitution", texto)
		}
	})

	t.Run("nome_pedido_nao_come_o_nome_de_participante_nao_pedido", func(t *testing.T) {
		// Achado da rodada 3: ordenar por comprimento so desempata entre os
		// nomes PEDIDOS. Aqui so "Ana" foi pedida, e "Ana Paula" e o nome de
		// outra participante — que matchMentionName nao devolve, porque nao
		// casa "Ana" por campo nenhum. Sem a lista de nomes do chat, "@Ana
		// Paula" virava "@<numero da Ana> Paula": a Ana grifada onde o autor
		// escreveu Ana Paula, em grupo, sem a D6 poder segurar.
		texto, jids, errMsg, _ := applyMentions(
			"bom dia @Ana e @Ana Paula",
			[]resolvedMention{curta},
			[]nomeDeParticipante{{nome: "Ana Paula", jid: "anapaula@s.whatsapp.net"}},
		)
		if errMsg != "" {
			t.Fatalf("unexpected refusal: %v", errMsg)
		}
		esperado := "bom dia @ana-user e @Ana Paula"
		if texto != esperado {
			t.Fatalf("texto = %q, want %q", texto, esperado)
		}
		if len(jids) != 1 {
			t.Fatalf("jids = %v, want only the requested mention", jids)
		}
	})

	t.Run("nome_pedido_nao_come_palavra_maior", func(t *testing.T) {
		// "@RodrigoPG" com mentions:["Rodrigo"] virava "@<numero>PG": texto
		// corrompido e mencao sem ancora valida. A D5 promete que "@" nao
		// pedido passa intacto — e passava so o que nao COMECAVA com um nome
		// pedido.
		rodrigo := resolvedMention{name: "Rodrigo", phoneUser: "rodrigo-user", jid: "rodrigo@s.whatsapp.net"}
		texto, _, errMsg, statusCode := applyMentions(
			"fala com @RodrigoPG",
			[]resolvedMention{rodrigo},
			nil,
		)
		if errMsg == "" || statusCode < 400 || statusCode >= 500 {
			t.Fatalf("errMsg=%q statusCode=%d, want a 4xx refusal — there is no valid anchor", errMsg, statusCode)
		}
		if strings.Contains(texto, "rodrigo-user") {
			t.Fatalf("texto = %q, want @RodrigoPG left intact", texto)
		}
	})

	t.Run("pontuacao_encerra_o_nome", func(t *testing.T) {
		texto, _, errMsg, _ := applyMentions(
			"@Ana, bom dia",
			[]resolvedMention{curta},
			nil,
		)
		if errMsg != "" {
			t.Fatalf("unexpected refusal: %v", errMsg)
		}
		if texto != "@ana-user, bom dia" {
			t.Fatalf("texto = %q", texto)
		}
	})

	t.Run("letra_acentuada_logo_depois_nao_encerra_o_nome", func(t *testing.T) {
		// Todo texto passado a applyMentions nos testes era ASCII puro, entao
		// uma fronteira feita byte a byte passaria na bateria inteira e
		// reintroduziria o defeito para nome com acento — num repositorio em
		// pt-BR. "Anailza" com i acentuado comeca com "Ana", e o primeiro byte
		// da rune acentuada nao e letra ASCII.
		texto, _, errMsg, statusCode := applyMentions(
			"fala com @Anaílza",
			[]resolvedMention{curta},
			nil,
		)
		if errMsg == "" || statusCode < 400 || statusCode >= 500 {
			t.Fatalf("errMsg=%q statusCode=%d, want a 4xx refusal — there is no valid anchor", errMsg, statusCode)
		}
		if strings.Contains(texto, "ana-user") {
			t.Fatalf("texto = %q, want the accented word left intact", texto)
		}
	})

	t.Run("nome_completo_da_propria_pessoa_pedida_ancora_a_mencao", func(t *testing.T) {
		// Achado da rodada 4: o nome longo da PROPRIA pessoa pedida vencia por
		// comprimento, ficava intacto, e a mencao saia sem uso — 400 dizendo
		// que "@Ana Paula" nao esta no texto, com "@Ana Paula" no texto.
		texto, jids, errMsg, _ := applyMentions(
			"bom dia @Ana Paula Souza",
			[]resolvedMention{longa},
			[]nomeDeParticipante{
				{nome: "Ana Paula Souza", jid: longa.jid},
				{nome: "Ana Paula", jid: longa.jid},
			},
		)
		if errMsg != "" {
			t.Fatalf("unexpected refusal: %v", errMsg)
		}
		if texto != "bom dia @anapaula-user" {
			t.Fatalf("texto = %q, want the full name to anchor the mention", texto)
		}
		if len(jids) != 1 || jids[0] != longa.jid {
			t.Fatalf("jids = %v, want the requested mention", jids)
		}
	})

	t.Run("mencao_sem_ancora_no_texto_recusa", func(t *testing.T) {
		texto, jids, errMsg, statusCode := applyMentions(
			"bom dia, pessoal",
			[]resolvedMention{curta},
			nil,
		)
		if errMsg == "" || statusCode < 400 || statusCode >= 500 {
			t.Fatalf("errMsg=%q statusCode=%d, want a 4xx refusal", errMsg, statusCode)
		}
		if jids != nil {
			t.Fatalf("jids = %v, want nil — nothing may be sent", jids)
		}
		if texto == "" {
			t.Fatalf("texto vazio; a recusa devolve o texto de entrada")
		}
	})
}

// TestFillSenderNamesPorLID cobre a observacao 4 da revisao de 2026-09-09:
// linhas de `senders` sao gravadas com resolveToPN, que devolve o @lid
// INALTERADO quando nao ha mapeamento PN. Medido no store real da conta
// pessoal em 2026-09-09: 637 de 2518 linhas com chave @lid, 587 delas com
// nome. Procurar so pela forma de telefone deixa essas pessoas invisiveis
// para o casamento por nome — e, pior, faz a D6 contar menos candidatos do
// que existem, mencionando a pessoa errada sem perguntar.
func TestFillSenderNamesPorLID(t *testing.T) {
	dir := t.TempDir()
	db, err := sql.Open("sqlite3", filepath.Join(dir, "messages.db"))
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer db.Close()
	if _, err := db.Exec(`CREATE TABLE senders (
		jid TEXT PRIMARY KEY, push_name TEXT, full_name TEXT, first_name TEXT, business_name TEXT
	)`); err != nil {
		t.Fatalf("create: %v", err)
	}
	const lid = "participante-so-por-lid@lid"
	if _, err := db.Exec(
		"INSERT INTO senders (jid, push_name, full_name, first_name, business_name) VALUES (?, ?, ?, ?, ?)",
		lid, "", "Ana Paula Souza", "Ana Paula", "",
	); err != nil {
		t.Fatalf("insert: %v", err)
	}
	store := &MessageStore{db: db}

	t.Run("sem_linha_PN_cai_no_lid", func(t *testing.T) {
		p := mentionParticipant{jid: "participante-b@s.whatsapp.net", outrasChaves: []string{lid}, phoneUser: "participante-b"}
		store.fillSenderNames(&p)
		if p.firstName != "Ana Paula" || p.fullName != "Ana Paula Souza" {
			t.Fatalf("firstName=%q fullName=%q, want the names found under the @lid row", p.firstName, p.fullName)
		}
		if len(matchMentionName([]mentionParticipant{p}, "Ana Paula")) != 1 {
			t.Fatalf("want the participant matchable by name once the @lid row is read")
		}
	})

	t.Run("campo_que_falta_na_linha_PN_vem_da_linha_lid", func(t *testing.T) {
		// Achado 2 da rodada 9: a busca parava na primeira linha que tivesse
		// QUALQUER nome. Linha PN so com push_name e linha @lid com o nome
		// completo: o nome longo se perdia e deixava de proteger o prefixo.
		const pnCurto = "participante-d@s.whatsapp.net"
		const lidLongo = "participante-d@lid"
		for _, ins := range [][]any{
			{pnCurto, "AnaP", "", "", ""},
			{lidLongo, "", "Ana Paula", "Ana", ""},
		} {
			if _, err := db.Exec(
				"INSERT INTO senders (jid, push_name, full_name, first_name, business_name) VALUES (?, ?, ?, ?, ?)",
				ins...,
			); err != nil {
				t.Fatalf("insert: %v", err)
			}
		}

		p := mentionParticipant{jid: pnCurto, outrasChaves: []string{lidLongo}, phoneUser: "participante-d"}
		store.fillSenderNames(&p)

		if p.pushName != "AnaP" {
			t.Fatalf("pushName = %q, want o da linha PN", p.pushName)
		}
		if p.fullName != "Ana Paula" {
			t.Fatalf("fullName = %q, want o da linha @lid — sem ele o prefixo fica desprotegido", p.fullName)
		}
	})

	t.Run("linha_PN_ganha_do_lid", func(t *testing.T) {
		const pn = "participante-c@s.whatsapp.net"
		if _, err := db.Exec(
			"INSERT INTO senders (jid, push_name, full_name, first_name, business_name) VALUES (?, ?, ?, ?, ?)",
			pn, "", "Nome Pela Forma PN", "Nome", "",
		); err != nil {
			t.Fatalf("insert: %v", err)
		}
		p := mentionParticipant{jid: pn, outrasChaves: []string{lid}, phoneUser: "participante-c"}
		store.fillSenderNames(&p)
		if p.fullName != "Nome Pela Forma PN" {
			t.Fatalf("fullName = %q, want the PN row to win", p.fullName)
		}
	})
}
