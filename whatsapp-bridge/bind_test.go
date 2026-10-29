package main

import (
	"errors"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"
)

// A second bridge on a port that is already taken must exit before it opens the
// store, so it never gets as far as connecting to WhatsApp with the same session
// (#49). Runs the real main() in a child process (this test binary re-executed)
// whose working directory is empty: the store directory appearing there means
// main went past the bind.
func TestBridgeExitsBeforeStoreWhenPortIsTaken(t *testing.T) {
	if os.Getenv("BIND_TEST_RUN_MAIN") == "1" {
		main()
		return
	}

	taken, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer taken.Close()
	port := taken.Addr().(*net.TCPAddr).Port

	dir := t.TempDir()
	cmd := exec.Command(os.Args[0], "-test.run=^TestBridgeExitsBeforeStoreWhenPortIsTaken$")
	cmd.Dir = dir
	cmd.Env = append(os.Environ(),
		"BIND_TEST_RUN_MAIN=1",
		fmt.Sprintf("WHATSAPP_BRIDGE_PORT=%d", port),
		"BIND_ADDR=",
	)
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()

	select {
	case err = <-done:
	case <-time.After(20 * time.Second):
		cmd.Process.Kill()
		<-done
		t.Fatal("bridge kept running on a taken port instead of exiting")
	}

	var exitErr *exec.ExitError
	if !errors.As(err, &exitErr) || exitErr.ExitCode() != 1 {
		t.Fatalf("want exit code 1, got %v", err)
	}
	if _, statErr := os.Stat(filepath.Join(dir, "store")); !os.IsNotExist(statErr) {
		t.Fatalf("store directory was created before the bind failed (stat: %v)", statErr)
	}
}
