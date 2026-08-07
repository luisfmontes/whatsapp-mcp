package main

import (
	"os"
	"runtime"
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
	if _, err := db.Exec(`INSERT INTO t VALUES ('Ação'), ('SÃO PAULO'), ('Luís')`); err != nil {
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
