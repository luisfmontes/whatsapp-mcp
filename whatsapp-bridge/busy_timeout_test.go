package main

import "testing"

// TestMessagesDBBusyTimeout guards against messages.db being opened without a
// busy_timeout. Two writers share the file (the bridge and transcribe.py), and
// history sync holds the write lock for long stretches, so a connection with no
// timeout fails immediately with "database is locked (SQLITE_BUSY)" instead of
// waiting its turn. whatsapp.db already had the pragma; messages.db did not.
func TestMessagesDBBusyTimeout(t *testing.T) {
	const want = 5000

	// setupChatStore chdirs into a temp dir and opens the store through the
	// real constructor, so the DSN under test is the production one.
	store := setupChatStore(t)

	t.Run("write_handle", func(t *testing.T) {
		var got int
		if err := store.db.QueryRow("PRAGMA busy_timeout").Scan(&got); err != nil {
			t.Fatalf("PRAGMA busy_timeout: %v", err)
		}
		if got != want {
			t.Fatalf("NewMessageStore busy_timeout = %d, want %d", got, want)
		}
	})

	t.Run("unaccent_read_handle", func(t *testing.T) {
		db, err := openUnaccentMessagesDB()
		if err != nil {
			t.Fatalf("openUnaccentMessagesDB: %v", err)
		}
		t.Cleanup(func() { _ = db.Close() })
		var got int
		if err := db.QueryRow("PRAGMA busy_timeout").Scan(&got); err != nil {
			t.Fatalf("PRAGMA busy_timeout: %v", err)
		}
		if got != want {
			t.Fatalf("openUnaccentMessagesDB busy_timeout = %d, want %d", got, want)
		}
	})
}
