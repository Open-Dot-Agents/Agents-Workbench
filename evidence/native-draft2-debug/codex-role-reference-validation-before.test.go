package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestNativeCodexRoleReferenceImportRelocation(t *testing.T) {
	for _, scope := range []string{"project", "user"} {
		for _, location := range []string{"external", "managed", "absolute", "declared-only"} {
			t.Run(scope+"/"+location, func(t *testing.T) {
				source, target, canonical := t.TempDir(), t.TempDir(), t.TempDir()
				t.Setenv("XDG_STATE_HOME", t.TempDir())
				configDir := source
				if scope == "project" { configDir = filepath.Join(source, ".codex"); canonical = source }
				folder := "role-library"
				if location == "managed" || location == "declared-only" { folder = "agents" }
				role := filepath.Join(configDir, folder, "fixture.toml")
				body := nativeAgentFixture
				if location == "declared-only" { body = "developer_instructions='Fixture.'\nmodel='child-model'\n" }
				writeFixture(t, role, body)
				reference := filepath.ToSlash(filepath.Join(folder, "fixture.toml"))
				if location == "absolute" { reference = role }
				content := "[agents.fixture]\ndescription='Fixture.'\nconfig_file='"+reference+"'\n"
				writeFixture(t, filepath.Join(configDir, "config.toml"), content)
				options := WriteOptions{Experimental: true, Scope: scope}
				apply := ApplyOptions{Experimental: true, Scope: scope}
				if scope == "user" { options.NativeHome, apply.NativeHome = source, target }
				if err := ImportRepositoryWithOptions("codex", canonical, options); err != nil { t.Fatal(err) }
				imported := filepath.Join(canonical, ".agents/native/com.openai.codex/config.toml")
				values, err := parseNative([]byte(readNativeTest(t, imported)), "toml")
				if err != nil { t.Fatal(err) }
				expected := role
				if location == "managed" { expected = "agents/fixture.toml" }
				got := values["agents"].(map[string]any)["fixture"].(map[string]any)["config_file"]
				if got != expected { t.Fatalf("reference changed location: got %v, want %s", got, expected) }
				if scope == "project" {
					// Copy only canonical configuration into a separate target project.
					if err := filepath.WalkDir(filepath.Join(canonical, ".agents"), func(path string, entry os.DirEntry, err error) error {
						if err != nil { return err }; if entry.IsDir() { return nil }
						relative, err := filepath.Rel(canonical, path); if err != nil { return err }
						writeFixture(t, filepath.Join(target, relative), readNativeTest(t, path)); return nil
					}); err != nil { t.Fatal(err) }
					canonical = target
				}
				if _, err := ApplyProjection("codex", canonical, apply); err != nil { t.Fatal(err) }
				if readNativeTest(t, role) != body || readNativeTest(t, filepath.Join(configDir, "config.toml")) != content { t.Fatal("source changed") }
			})
		}
	}
}
