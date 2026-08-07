//go:build windows

package main

import (
	"os/exec"
	"path/filepath"

	"golang.org/x/sys/windows"
)

// Windows implementations of the handful of OS-specific operations the bridge
// needs. The macOS/Linux versions live in platform_unix.go; both files expose
// the same three functions so main.go stays platform-agnostic.

// stillActive is the exit code Windows reports for a process that has not
// exited yet (STILL_ACTIVE).
const stillActive = 259

// openInDefaultApp asks the shell to open path with whatever application is
// registered for its type — the equivalent of `open` on macOS. rundll32 is used
// instead of `cmd /c start` because the latter treats the first quoted argument
// as a window title, which silently breaks on paths containing spaces.
func openInDefaultApp(path string) error {
	return exec.Command("rundll32", "url.dll,FileProtocolHandler", path).Start()
}

// processAlive reports whether pid belongs to a running process.
//
// The POSIX idiom (os.FindProcess + Signal(0)) cannot be used here: on Windows
// os.FindProcess succeeds for any PID without checking anything, and Signal is
// unimplemented — so the POSIX version always answers "dead" and any lock guard
// built on it silently stops guarding.
//
// Caveat kept deliberately: a process that exits with code 259 is
// indistinguishable from a running one, and Windows reuses PIDs. Good enough
// for an advisory lockfile, not for anything requiring certainty.
func processAlive(pid int) bool {
	h, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(pid))
	if err != nil {
		return false
	}
	defer windows.CloseHandle(h)

	var code uint32
	if err := windows.GetExitCodeProcess(h, &code); err != nil {
		return false
	}
	return code == stillActive
}

// venvPython returns the interpreter inside a uv/virtualenv environment.
// Windows puts it in Scripts\python.exe, not bin/python3.
func venvPython(pyDir string) string {
	return filepath.Join(pyDir, ".venv", "Scripts", "python.exe")
}
