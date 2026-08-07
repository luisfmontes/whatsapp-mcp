//go:build !windows

package main

import (
	"database/sql"
	"fmt"

	sqlite3 "github.com/mattn/go-sqlite3"
)

// init registers the "sqlite3_unaccent" driver on non-Windows platforms.
// This driver wraps the mattn/go-sqlite3 driver and registers the "unaccent"
// SQL scalar function on every new connection, mirroring the Python side's
// conn.create_function("unaccent", 1, _strip_accents).
func init() {
	sql.Register("sqlite3_unaccent", &sqlite3.SQLiteDriver{
		ConnectHook: func(conn *sqlite3.SQLiteConn) error {
			return conn.RegisterFunc("unaccent", stripAccents, true)
		},
	})
}

// openMessagesDB opens the main messages database for writing (main.go line 77).
// Uses the plain "sqlite3" driver from mattn/go-sqlite3 with foreign key constraints enabled.
func openMessagesDB() (*sql.DB, error) {
	return sql.Open("sqlite3", "file:store/messages.db?_foreign_keys=on")
}

// openUnaccentMessagesDB opens a read-only connection to messages.db with unaccent support.
// Uses the registered "sqlite3_unaccent" driver that has the unaccent function available.
// Kept separate from messageStore.db (which uses the plain "sqlite3" driver) to avoid
// touching the existing write path.
func openUnaccentMessagesDB() (*sql.DB, error) {
	return sql.Open("sqlite3_unaccent", "file:store/messages.db?_foreign_keys=on")
}

// openStoreDBReadOnly opens a read-only connection to whatsmeow's session/contacts database.
// whatsmeow's sqlstore.Container doesn't expose raw SQL access, so reads needed for
// LID/contact resolution use their own handle here. mode=ro avoids interfering with
// whatsmeow's writes; _busy_timeout tolerates brief lock contention instead of failing immediately.
func openStoreDBReadOnly() (*sql.DB, error) {
	return sql.Open("sqlite3", "file:store/whatsapp.db?mode=ro&_busy_timeout=5000")
}

// storeDSN returns the database connection string for whatsmeow's sqlstore.New().
// Must use "sqlite3" as the driver name (not "sqlite") so whatsmeow's dialect detection
// works correctly and chooses the SQLite UPSERT syntax.
func storeDSN() string {
	return "file:store/whatsapp.db?_foreign_keys=on"
}
