//go:build !windows

package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"syscall"
)

// macOS/Linux implementations of the OS-specific operations. See
// platform_windows.go for the Windows half.

// openInDefaultApp asks the desktop to open path with its default application.
// macOS has `open`; Linux desktops use `xdg-open` (the previous code called
// `open` everywhere, which meant this silently did nothing on Linux).
func openInDefaultApp(path string) error {
	opener := "xdg-open"
	if runtime.GOOS == "darwin" {
		opener = "open"
	}
	return exec.Command(opener, path).Start()
}

// processAlive reports whether pid belongs to a running process, using the
// POSIX signal-0 probe: it performs permission and existence checks without
// delivering anything.
func processAlive(pid int) bool {
	proc, err := os.FindProcess(pid)
	if err != nil {
		return false
	}
	return proc.Signal(syscall.Signal(0)) == nil
}

// venvPython returns the interpreter inside a uv/virtualenv environment.
func venvPython(pyDir string) string {
	return filepath.Join(pyDir, ".venv", "bin", "python3")
}
