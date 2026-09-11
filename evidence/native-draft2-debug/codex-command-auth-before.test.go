package config

import (
	"fmt"
	"os"
	"path/filepath"
	"testing"
)

func TestNativeProviderCommandAuthenticationRoundTrip(t *testing.T) {
	home, target, repo, again := t.TempDir(), t.TempDir(), t.TempDir(), t.TempDir()
	t.Setenv("XDG_STATE_HOME", t.TempDir())
	commandDir := t.TempDir()
	marker := filepath.Join(commandDir, "executed")
	writeFixture(t, filepath.Join(commandDir, "command.sh"), "touch executed\n")
	config := fmt.Sprintf("model_provider='demo'\n[model_providers.demo]\nname='Demo'\nbase_url='https://example.test/v1'\n[model_providers.demo.auth]\ncommand='/bin/sh'\nargs=['command.sh', 'literal;argument']\ncwd=%q\ntimeout_ms=1000\nrefresh_interval_ms=0\n", commandDir)
	writeFixture(t, filepath.Join(home, "config.toml"), config)
	if err := ImportRepositoryWithOptions("codex", repo, WriteOptions{Experimental: true, Scope: "user", NativeHome: home}); err != nil {
		t.Fatalf("valid token-command configuration was rejected: %v", err)
	}
	if _, err := ApplyProjection("codex", repo, ApplyOptions{Experimental: true, Scope: "user", NativeHome: target}); err != nil { t.Fatal(err) }
	if err := ImportRepositoryWithOptions("codex", again, WriteOptions{Experimental: true, Scope: "user", NativeHome: target}); err != nil { t.Fatal(err) }
	name := ".agents/native/com.openai.codex/config.toml"
	want, err := parseNative([]byte(config), "toml")
	if err != nil { t.Fatal(err) }
	for _, path := range []string{filepath.Join(repo, name), filepath.Join(target, "config.toml"), filepath.Join(again, name)} {
		got, err := parseNative([]byte(readNativeTest(t, path)), "toml")
		if err != nil || nativeHash(want) != nativeHash(got) { t.Fatalf("token-command configuration changed: %s: %v", path, err) }
	}
	if _, err := os.Stat(marker); !os.IsNotExist(err) { t.Fatal("configuration operation executed the token command") }
	if readNativeTest(t, filepath.Join(home, "config.toml")) != config { t.Fatal("native source changed") }
}
