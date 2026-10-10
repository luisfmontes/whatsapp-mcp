package main

import (
	"encoding/json"
	"net/http"
	"testing"
	"time"
)

// TestHandleHistoryRequest covers POST /api/history_request (issue #40): the
// request-validation paths, the anchor lookup against a real store, and the
// nil-client 503 — the only path short of a live phone that proves the
// handler reaches requestHistorySync with a resolved anchor.
func TestHandleHistoryRequest(t *testing.T) {
	const chatJID = "contato-historico@s.whatsapp.net"
	const anchorID = "MSG-HISTORY-ANCHOR"

	decode := func(t *testing.T, body []byte) MarkChatResponse {
		t.Helper()
		var resp MarkChatResponse
		if err := json.Unmarshal(body, &resp); err != nil {
			t.Fatalf("decode: %v; body=%s", err, body)
		}
		return resp
	}

	t.Run("metodo_errado", func(t *testing.T) {
		rec := doHandlerRequest(t, handleHistoryRequest(nil, nil), http.MethodGet, nil)
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("status = %d, want 405", rec.Code)
		}
	})

	t.Run("campos_obrigatorios", func(t *testing.T) {
		for _, body := range []string{`{`, `{"chat_jid":"` + chatJID + `"}`, `{"message_id":"` + anchorID + `"}`} {
			rec := doHandlerRequest(t, handleHistoryRequest(nil, nil), http.MethodPost, []byte(body))
			if rec.Code != http.StatusBadRequest {
				t.Errorf("body %s: status = %d, want 400", body, rec.Code)
			}
		}
	})

	t.Run("count_fora_do_limite", func(t *testing.T) {
		for _, count := range []int{-1, historyRequestMaxCount + 1} {
			body, _ := json.Marshal(HistoryRequest{ChatJID: chatJID, MessageID: anchorID, Count: count})
			rec := doHandlerRequest(t, handleHistoryRequest(nil, nil), http.MethodPost, body)
			if rec.Code != http.StatusBadRequest {
				t.Errorf("count %d: status = %d, want 400", count, rec.Code)
			}
		}
	})

	t.Run("ancora_fora_do_store", func(t *testing.T) {
		store := setupPollStore(t)
		body, _ := json.Marshal(HistoryRequest{ChatJID: chatJID, MessageID: "MSG-QUE-NAO-EXISTE"})
		rec := doHandlerRequest(t, handleHistoryRequest(nil, store), http.MethodPost, body)
		if rec.Code != http.StatusNotFound {
			t.Fatalf("status = %d, want 404; body=%s", rec.Code, rec.Body.String())
		}
		if resp := decode(t, rec.Body.Bytes()); resp.Success {
			t.Errorf("Success = true for an unknown anchor")
		}
	})

	t.Run("ancora_no_store_sem_cliente_da_503", func(t *testing.T) {
		store := setupPollStore(t)
		ts := time.Date(2026, 10, 9, 17, 26, 55, 0, time.UTC)
		if err := store.StoreChat(chatJID, "Contato", ts); err != nil {
			t.Fatalf("StoreChat: %v", err)
		}
		if err := store.StoreMessage(anchorID, chatJID, "me", "Como assim?", ts, true,
			"", "", "", nil, nil, nil, 0); err != nil {
			t.Fatalf("StoreMessage: %v", err)
		}
		body, _ := json.Marshal(HistoryRequest{ChatJID: chatJID, MessageID: anchorID, Count: 20})
		rec := doHandlerRequest(t, handleHistoryRequest(nil, store), http.MethodPost, body)
		if rec.Code != http.StatusServiceUnavailable {
			t.Fatalf("status = %d, want 503; body=%s", rec.Code, rec.Body.String())
		}
	})

	t.Run("get_message_anchor_devolve_autor_e_hora", func(t *testing.T) {
		store := setupPollStore(t)
		ts := time.Date(2026, 10, 9, 16, 59, 54, 0, time.UTC)
		if err := store.StoreChat(chatJID, "Contato", ts); err != nil {
			t.Fatalf("StoreChat: %v", err)
		}
		if err := store.StoreMessage(anchorID, chatJID, "contato", "pode compilar", ts, false,
			"", "", "", nil, nil, nil, 0); err != nil {
			t.Fatalf("StoreMessage: %v", err)
		}
		isFromMe, got, err := store.GetMessageAnchor(anchorID, chatJID)
		if err != nil {
			t.Fatalf("GetMessageAnchor: %v", err)
		}
		if isFromMe {
			t.Errorf("isFromMe = true, want false")
		}
		if !got.Equal(ts) {
			t.Errorf("timestamp = %v, want %v", got, ts)
		}
	})
}
