package main

import (
	"database/sql"
	"fmt"
	"os"
	"runtime"
	"strings"
	"testing"
)

// TestSQLiteLayer exercises the platform SQLite layer (sqlite_driver_cgo.go on
// macOS/Linux, sqlite_driver_windows.go on Windows) through the same assertions
// on both, so the two implementations cannot silently diverge.
//
// It exists because none of this is caught at compile time: DSN parameters are
// parsed at connect time, and a driver instance that fails to expose the
// unaccent() function compiles and connects perfectly while breaking every
// search endpoint at runtime.
func TestSQLiteLayer(t *testing.T) {
	orig, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	// The DSNs are relative to the working directory ("file:store/…"), so the
	// test has to run inside a scratch dir. Windows cannot delete a directory
	// that is a process's CWD, and cleanups run LIFO — registering this after
	// t.TempDir means it runs before TempDir's removal.
	t.Cleanup(func() { _ = os.Chdir(orig) })
	if err := os.Chdir(dir); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll("store", 0o755); err != nil {
		t.Fatal(err)
	}

	// --- write handle -------------------------------------------------------
	db, err := openMessagesDB()
	if err != nil {
		t.Fatalf("openMessagesDB: %v", err)
	}
	var fk int
	if err := db.QueryRow("PRAGMA foreign_keys").Scan(&fk); err != nil {
		t.Fatalf("PRAGMA foreign_keys: %v", err)
	}
	if fk != 1 {
		t.Errorf("foreign_keys = %d, want 1 — the DSN's foreign-key flag was ignored", fk)
	}
	if _, err := db.Exec(`CREATE TABLE t (name TEXT)`); err != nil {
		t.Fatalf("create: %v", err)
	}
	// The NULL row is the point of this fixture, not padding: chats.name and
	// messages.content are nullable TEXT and are exactly what the search
	// endpoints pass to unaccent().
	if _, err := db.Exec(`INSERT INTO t VALUES ('Ação'), ('SÃO PAULO'), ('Luís'), (NULL)`); err != nil {
		t.Fatalf("insert: %v", err)
	}
	db.Close()

	// --- read handle: the unaccent() SQL function --------------------------
	rdb, err := openUnaccentMessagesDB()
	if err != nil {
		t.Fatalf("openUnaccentMessagesDB: %v", err)
	}
	var folded string
	if err := rdb.QueryRow(`SELECT unaccent('ÁÇão ÕÜ')`).Scan(&folded); err != nil {
		// The failure mode this guards: a driver instance that does not carry
		// the registered scalar function ("no such function: unaccent").
		t.Fatalf("unaccent scalar: %v", err)
	}
	if folded != "acao ou" {
		t.Errorf("unaccent('ÁÇão ÕÜ') = %q, want %q", folded, "acao ou")
	}

	// Rows fold to 'acao', 'sao paulo', 'luis'.
	for _, tc := range []struct {
		pattern string
		want    int
		proves  string
	}{
		{"%SÃO%", 1, "pattern side: case + tilde folded before matching"},
		{"%luis%", 1, "column side: unaccented query finds the accented row"},
		{"%ao%", 2, "plain substring still matches both -ao rows"},
	} {
		var n int
		if err := rdb.QueryRow(`SELECT count(*) FROM t WHERE unaccent(name) LIKE unaccent(?)`, tc.pattern).Scan(&n); err != nil {
			t.Fatalf("unaccent in WHERE (%s): %v", tc.pattern, err)
		}
		if n != tc.want {
			t.Errorf("pattern %s matched %d rows, want %d (%s)", tc.pattern, n, tc.want, tc.proves)
		}
	}
	// NULL must not blow up the query. Registering a string-only function makes
	// the CGO driver reject a NULL argument outright ("argument must be BLOB or
	// TEXT"), which took down list_chats and text search on macOS/Linux while
	// working on Windows. The two halves spell the result differently — NULL on
	// Windows, empty string under CGO — so assert what actually matters: no
	// error, and no spurious match.
	var nullFolded sql.NullString
	if err := rdb.QueryRow(`SELECT unaccent(NULL)`).Scan(&nullFolded); err != nil {
		t.Errorf("unaccent(NULL) errored: %v", err)
	}
	var nullRowMatches int
	if err := rdb.QueryRow(`SELECT count(*) FROM t WHERE name IS NULL AND unaccent(name) LIKE unaccent(?)`, "%a%").Scan(&nullRowMatches); err != nil {
		t.Errorf("search over a NULL row errored: %v", err)
	} else if nullRowMatches != 0 {
		t.Errorf("NULL row matched a LIKE pattern %d times, want 0", nullRowMatches)
	}

	rdb.Close()

	// --- read-only handle on whatsmeow's own database ----------------------
	if f, err := os.Create("store/whatsapp.db"); err == nil {
		f.Close()
	} else {
		t.Fatal(err)
	}
	sdb, err := openStoreDBReadOnly()
	if err != nil {
		t.Fatalf("openStoreDBReadOnly: %v", err)
	}
	defer sdb.Close()
	if _, err := sdb.Exec(`CREATE TABLE should_not_exist (x INT)`); err == nil {
		t.Error("read-only handle accepted a write — it could corrupt whatsmeow's session store")
	}

	// The two platforms reach read-only-ness differently: mattn takes mode=ro,
	// modernc takes _pragma=query_only(true). Only the Windows spelling is
	// assertable as a PRAGMA read-back, so keep that check platform-scoped
	// rather than weakening it for both.
	if runtime.GOOS == "windows" {
		var bt, qo int
		if err := sdb.QueryRow("PRAGMA busy_timeout").Scan(&bt); err != nil {
			t.Fatalf("PRAGMA busy_timeout: %v", err)
		}
		if bt != 5000 {
			t.Errorf("busy_timeout = %d, want 5000 — lock contention would fail instantly", bt)
		}
		if err := sdb.QueryRow("PRAGMA query_only").Scan(&qo); err != nil {
			t.Fatalf("PRAGMA query_only: %v", err)
		}
		if qo != 1 {
			t.Errorf("query_only = %d, want 1", qo)
		}
	}
}

// TestMessageStoreIndexes checks the schema NewMessageStore creates. It lives
// next to the driver tests because both are about the storage layer, and because
// an index is only worth anything if the planner actually picks it — existing in
// sqlite_master is not the same as being used.
func TestMessageStoreIndexes(t *testing.T) {
	orig, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	t.Cleanup(func() { _ = os.Chdir(orig) })
	if err := os.Chdir(dir); err != nil {
		t.Fatal(err)
	}

	store, err := NewMessageStore()
	if err != nil {
		t.Fatalf("NewMessageStore: %v", err)
	}
	defer store.Close()

	for _, name := range []string{
		"idx_messages_chat_time",
		"idx_messages_audio_pending",
		"idx_senders_names",
	} {
		var found string
		err := store.db.QueryRow(
			"SELECT name FROM sqlite_master WHERE type='index' AND name=?", name).Scan(&found)
		if err != nil {
			t.Errorf("index %s not created: %v", name, err)
		}
	}

	// The reads that matter must resolve through the index instead of scanning.
	for _, tc := range []struct {
		name  string
		query string
		args  []any
		want  string
	}{
		{
			name:  "mensagens de um chat, mais recentes primeiro",
			query: "SELECT id FROM messages WHERE chat_jid=? ORDER BY timestamp DESC LIMIT 20",
			args:  []any{"x@g.us"},
			want:  "idx_messages_chat_time",
		},
		{
			name:  "audios pendentes de transcricao",
			query: "SELECT id FROM messages WHERE media_type='audio' AND (content IS NULL OR content='')",
			args:  nil,
			want:  "idx_messages_audio_pending",
		},
	} {
		rows, err := store.db.Query("EXPLAIN QUERY PLAN "+tc.query, tc.args...)
		if err != nil {
			t.Fatalf("%s: explain: %v", tc.name, err)
		}
		var plan strings.Builder
		cols, err := rows.Columns()
		if err != nil {
			t.Fatal(err)
		}
		for rows.Next() {
			cells := make([]any, len(cols))
			for i := range cells {
				cells[i] = new(any)
			}
			if err := rows.Scan(cells...); err != nil {
				t.Fatalf("%s: scan: %v", tc.name, err)
			}
			// The last column is the human-readable detail ("SEARCH ... USING INDEX ...").
			fmt.Fprintf(&plan, "%v ", *(cells[len(cells)-1].(*any)))
		}
		rows.Close()
		if !strings.Contains(plan.String(), tc.want) {
			t.Errorf("%s: plano nao usa %s\n  plano: %s", tc.name, tc.want, plan.String())
		}
	}
}
