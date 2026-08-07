//go:build windows

package main

import (
	"database/sql"
	"database/sql/driver"
	"fmt"

	"modernc.org/sqlite"
)

// Windows half of the SQLite layer. mattn/go-sqlite3 (used on macOS/Linux, see
// sqlite_driver_cgo.go) needs CGO and therefore a MinGW toolchain, which a
// stock Windows box does not have. modernc.org/sqlite is the same SQLite,
// transpiled to Go, so `CGO_ENABLED=0 go build` produces a standalone .exe.
//
// Two constraints shape this file:
//
//  1. The driver MUST be registered as "sqlite3". whatsmeow's sqlstore.New()
//     takes its `dialect` argument as BOTH the database/sql driver name AND the
//     switch for which UPSERT syntax to emit — pass modernc's own name
//     ("sqlite") and the session store breaks.
//
//  2. The alias MUST point at the driver instance modernc itself registered,
//     obtained via (*sql.DB).Driver(). A hand-built &sqlite.Driver{} compiles
//     and connects fine but does NOT see functions registered with
//     RegisterDeterministicScalarFunction — every unaccent() query fails at
//     runtime with "no such function: unaccent". Verified empirically; do not
//     "simplify" this back to a struct literal.
func init() {
	sqlite.MustRegisterDeterministicScalarFunction("unaccent", 1, unaccentFunc)

	probe, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		panic(fmt.Errorf("cannot reach modernc sqlite driver: %w", err))
	}
	drv := probe.Driver()
	_ = probe.Close()

	sql.Register("sqlite3", drv)
	// Same driver under the second name the read path uses, so both platforms
	// share identical call sites in main.go.
	sql.Register("sqlite3_unaccent", drv)
}

// unaccentFunc adapts stripAccents (main.go) to modernc's scalar-function
// signature, mirroring the ConnectHook registration on the CGO side.
func unaccentFunc(ctx *sqlite.FunctionContext, args []driver.Value) (driver.Value, error) {
	if len(args) != 1 {
		return nil, fmt.Errorf("unaccent expects 1 argument, got %d", len(args))
	}
	switch v := args[0].(type) {
	case nil:
		// SQL NULL in, SQL NULL out — matches SQLite's own behaviour for
		// string functions and mattn's RegisterFunc on the CGO side.
		return nil, nil
	case string:
		return stripAccents(v), nil
	case []byte:
		// TEXT can arrive as []byte depending on how the column was bound.
		// Without this case it would fall through to a %v formatting of the
		// byte slice ("[195 129 ...]") and silently corrupt every search.
		return stripAccents(string(v)), nil
	default:
		// Numbers/other types: match Python's str() coercion rather than error.
		return stripAccents(fmt.Sprintf("%v", v)), nil
	}
}

// openMessagesDB opens the read/write handle for messages.db.
// _fk=on is modernc's spelling of mattn's _foreign_keys=on (verified: PRAGMA
// foreign_keys reports 1 with this DSN). busy_timeout: transcribe.py writes
// transcriptions into this same file, so two writers do compete.
func openMessagesDB() (*sql.DB, error) {
	return sql.Open("sqlite3", "file:store/messages.db?_fk=on&_pragma=busy_timeout(5000)")
}

// openUnaccentMessagesDB opens the second handle used by the read endpoints,
// where search filters call unaccent(). Same driver, separate handle, so the
// write path is untouched.
//
// busy_timeout is not optional here: while history sync is writing, a search
// that scans enough rows loses the lock race and the endpoint returns
// "database is locked (5) (SQLITE_BUSY)" as a 500 instead of waiting. Observed
// on a live sync — list_messages/list_chats/search_contacts all fail that way.
func openUnaccentMessagesDB() (*sql.DB, error) {
	return sql.Open("sqlite3_unaccent", "file:store/messages.db?_fk=on&_pragma=busy_timeout(5000)")
}

// openStoreDBReadOnly opens whatsmeow's own session/contacts database for
// reading only (LID/contact resolution), since sqlstore.Container exposes no
// raw *sql.DB. query_only refuses writes from this handle so it cannot
// interfere with whatsmeow's writer; busy_timeout tolerates brief lock
// contention instead of failing immediately.
func openStoreDBReadOnly() (*sql.DB, error) {
	return sql.Open("sqlite3", "file:store/whatsapp.db?_pragma=query_only(true)&_pragma=busy_timeout(5000)")
}

// storeDSN is the DSN handed to sqlstore.New (which supplies "sqlite3" as the
// dialect/driver name separately).
func storeDSN() string {
	return "file:store/whatsapp.db?_fk=on"
}
