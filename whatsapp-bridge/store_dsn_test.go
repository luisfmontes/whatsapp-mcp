package main

import (
	"context"
	"database/sql"
	"os"
	"strings"
	"testing"
	"time"
)

// TestStoreDSNLockContention covers issue #41: whatsmeow's own handle on
// whatsapp.db (storeDSN) must not fail with SQLITE_BUSY when another handle
// touches the file. Before the fix the DSN had no busy_timeout and ran in
// rollback-journal mode, so a concurrent reader or writer made the write fail
// instantly — "failed to save identity", decryption aborted, message lost.
func TestStoreDSNLockContention(t *testing.T) {
	orig, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	t.Cleanup(func() { _ = os.Chdir(orig) })
	if err := os.Chdir(dir); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll("store", 0o755); err != nil {
		t.Fatal(err)
	}

	open := func(t *testing.T) *sql.DB {
		t.Helper()
		db, err := sql.Open("sqlite3", storeDSN())
		if err != nil {
			t.Fatalf("open storeDSN: %v", err)
		}
		t.Cleanup(func() { db.Close() })
		return db
	}
	writer := open(t)
	if _, err := writer.Exec(`CREATE TABLE IF NOT EXISTS identities (id TEXT PRIMARY KEY)`); err != nil {
		t.Fatalf("create: %v", err)
	}

	t.Run("pragmas_da_conexao", func(t *testing.T) {
		var bt int
		if err := writer.QueryRow("PRAGMA busy_timeout").Scan(&bt); err != nil {
			t.Fatalf("PRAGMA busy_timeout: %v", err)
		}
		if bt != 5000 {
			t.Errorf("busy_timeout = %d, want 5000", bt)
		}
		var mode string
		if err := writer.QueryRow("PRAGMA journal_mode").Scan(&mode); err != nil {
			t.Fatalf("PRAGMA journal_mode: %v", err)
		}
		if !strings.EqualFold(mode, "wal") {
			t.Errorf("journal_mode = %q, want wal", mode)
		}
	})

	t.Run("leitor_aberto_nao_derruba_a_gravacao", func(t *testing.T) {
		reader, err := openStoreDBReadOnly()
		if err != nil {
			t.Fatalf("openStoreDBReadOnly: %v", err)
		}
		defer reader.Close()
		ctx := context.Background()
		conn, err := reader.Conn(ctx)
		if err != nil {
			t.Fatal(err)
		}
		defer conn.Close()
		if _, err := conn.ExecContext(ctx, "BEGIN"); err != nil {
			t.Fatal(err)
		}
		var n int
		if err := conn.QueryRowContext(ctx, "SELECT count(*) FROM identities").Scan(&n); err != nil {
			t.Fatal(err)
		}
		if _, err := writer.Exec(`INSERT INTO identities VALUES ('com-leitor-aberto')`); err != nil {
			t.Errorf("write with an open reader failed: %v", err)
		}
		_, _ = conn.ExecContext(ctx, "ROLLBACK")
	})

	t.Run("outro_gravador_espera_em_vez_de_falhar", func(t *testing.T) {
		holder := open(t)
		ctx := context.Background()
		conn, err := holder.Conn(ctx)
		if err != nil {
			t.Fatal(err)
		}
		defer conn.Close()
		if _, err := conn.ExecContext(ctx, "BEGIN IMMEDIATE"); err != nil {
			t.Fatal(err)
		}
		released := make(chan struct{})
		go func() {
			time.Sleep(500 * time.Millisecond)
			_, _ = conn.ExecContext(ctx, "COMMIT")
			close(released)
		}()
		if _, err := writer.Exec(`INSERT INTO identities VALUES ('depois-do-lock')`); err != nil {
			t.Errorf("write behind a 500ms lock failed instead of waiting: %v", err)
		}
		<-released
	})
}
