package main

import (
	"context"
	"errors"
	"net/http"
	"reflect"
	"testing"

	"go.mau.fi/whatsmeow/types"
)

type fakePresenceSender struct {
	sent    []types.Presence
	failsOn types.Presence
}

func (f *fakePresenceSender) SendPresence(_ context.Context, state types.Presence) error {
	if state == f.failsOn {
		return errors.New("falha simulada")
	}
	f.sent = append(f.sent, state)
	return nil
}

// TestPulsePresence: the keepalive marks the device in use and always ends
// unavailable, so the phone keeps its notifications.
func TestPulsePresence(t *testing.T) {
	t.Run("available_depois_unavailable", func(t *testing.T) {
		f := &fakePresenceSender{}
		if err := pulsePresence(f, 0); err != nil {
			t.Fatalf("pulsePresence: %v", err)
		}
		want := []types.Presence{types.PresenceAvailable, types.PresenceUnavailable}
		if !reflect.DeepEqual(f.sent, want) {
			t.Fatalf("sent = %v, want %v", f.sent, want)
		}
	})

	t.Run("falha_no_available_nao_manda_unavailable", func(t *testing.T) {
		f := &fakePresenceSender{failsOn: types.PresenceAvailable}
		if err := pulsePresence(f, 0); err == nil {
			t.Fatal("pulsePresence returned nil, want the available error")
		}
		if len(f.sent) != 0 {
			t.Fatalf("sent = %v, want nothing", f.sent)
		}
	})
}

// TestHandlePresence covers POST /api/presence validation and the
// disconnected-client 503.
func TestHandlePresence(t *testing.T) {
	cases := []struct {
		name   string
		method string
		body   string
		want   int
	}{
		{"metodo_errado", http.MethodGet, "", http.StatusMethodNotAllowed},
		{"json_invalido", http.MethodPost, "{", http.StatusBadRequest},
		{"estado_invalido", http.MethodPost, `{"state":"composing"}`, http.StatusBadRequest},
		{"sem_cliente_da_503", http.MethodPost, `{"state":"available"}`, http.StatusServiceUnavailable},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			var body []byte
			if c.body != "" {
				body = []byte(c.body)
			}
			rec := doHandlerRequest(t, handlePresence(nil), c.method, body)
			if rec.Code != c.want {
				t.Fatalf("status = %d, want %d; body=%s", rec.Code, c.want, rec.Body.String())
			}
		})
	}
}
