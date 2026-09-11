package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestNativeRecursiveInstructionRoundTrip(t *testing.T) {
	for _, scope := range []string{"project", "user"} {
		t.Run(scope, func(t *testing.T) {
			t.Setenv("XDG_STATE_HOME", t.TempDir())
			source, home, target := t.TempDir(), t.TempDir(), t.TempDir()
			base := filepath.Join(source, ".github/instructions")
			importOptions := WriteOptions{Experimental: true, Scope: scope}
			options := ApplyOptions{Experimental: true, Scope: scope}
			if scope == "user" {
				base = filepath.Join(home, "instructions")
				importOptions.NativeHome = home
				options.NativeHome = t.TempDir()
			}
			files := map[string]string{
				"same.instructions.md":             "---\napplyTo: '**'\n---\nFlat instructions.\n",
				"nested/deep/same.instructions.md": "---\napplyTo: '**/*.go'\n---\nNested instructions.\n",
			}
			for name, body := range files {
				writeFixture(t, filepath.Join(base, name), body)
			}
			writeFixture(t, filepath.Join(base, "nested/notes.txt"), "Excluded source.")
			if err := ImportRepositoryWithOptions("copilot", source, importOptions); err != nil {
				t.Fatal(err)
			}
			canonical := filepath.Join(source, ".agents/native/com.github.copilot")
			for name, body := range files {
				if readNativeTest(t, filepath.Join(canonical, "scoped-instructions", name)) != body {
					t.Fatal("recursive source lost")
				}
			}
			if _, err := os.Stat(filepath.Join(canonical, "scoped-instructions/nested/notes.txt")); !os.IsNotExist(err) {
				t.Fatal("unrecognized file imported")
			}
			if !strings.Contains(readNativeTest(t, filepath.Join(canonical, "import-report.json")), "nested/notes.txt") {
				t.Fatal("excluded source not reported")
			}
			if err := os.CopyFS(filepath.Join(target, ".agents"), os.DirFS(filepath.Join(source, ".agents"))); err != nil {
				t.Fatal(err)
			}
			if _, err := ApplyProjection("copilot", target, options); err != nil {
				t.Fatal(err)
			}
			projected := filepath.Join(target, ".github/instructions")
			if scope == "user" {
				projected = filepath.Join(options.NativeHome, "instructions")
			}
			for name, body := range files {
				path := filepath.Join(projected, name)
				if readNativeTest(t, path) != body {
					t.Fatal("projection changed source")
				}
				if info, err := os.Stat(path); err != nil || info.Mode().Perm() != 0600 {
					t.Fatal("new file is not private")
				}
			}
			plan, err := PlanProjection("copilot", target, options)
			if err != nil || len(plan.Actions) != 0 {
				t.Fatalf("repeat projection: %v %+v", err, plan)
			}
			importOptions.NativeHome = options.NativeHome
			if err := ImportRepositoryWithOptions("copilot", target, importOptions); err != nil {
				t.Fatal(err)
			}
			for name, body := range files {
				if readNativeTest(t, filepath.Join(target, ".agents/native/com.github.copilot/scoped-instructions", name)) != body {
					t.Fatal("reimport changed source")
				}
				if readNativeTest(t, filepath.Join(base, name)) != body {
					t.Fatal("original native source changed")
				}
			}
			// A different source cannot take the nested user asset through --force.
			if scope == "user" {
				other := t.TempDir()
				if err := os.CopyFS(filepath.Join(other, ".agents"), os.DirFS(filepath.Join(source, ".agents"))); err != nil {
					t.Fatal(err)
				}
				forced := options
				forced.Force = true
				if _, err := ApplyProjection("copilot", other, forced); err == nil {
					t.Fatal("another repository took ownership")
				}
			}
			profilePath := filepath.Join(target, ".agents/native/com.github.copilot/profile.json")
			var profile nativeProfile
			if err := decodePolicy(profilePath, &profile); err != nil {
				t.Fatal(err)
			}
			profile.Artifacts = []nativeArtifact{}
			data, _ := json.Marshal(profile)
			writeFixture(t, profilePath, string(data))
			options.Backup = true
			if _, err := ApplyProjection("copilot", target, options); err != nil {
				t.Fatal(err)
			}
			for name, body := range files {
				path := filepath.Join(projected, name)
				if _, err := os.Stat(path); !os.IsNotExist(err) {
					t.Fatal("owned nested asset not removed")
				}
				if readNativeTest(t, path+".bak") != body {
					t.Fatal("removed source not backed up")
				}
			}
		})
	}
}

func TestNativeRecursiveInstructionPaths(t *testing.T) {
	for _, name := range []string{"../escape.instructions.md", "/absolute.instructions.md", "nested/../../escape.instructions.md", "nested/../flat.instructions.md", "nested/link\\file.instructions.md", "nested/other.md"} {
		if _, _, err := nativeTargetPath("copilot", "project", t.TempDir(), nativeArtifact{Kind: "scoped-instructions", Name: name}); err == nil {
			t.Fatalf("unsafe name accepted: %s", name)
		}
	}
	for _, kind := range []string{"agent", "hooks"} {
		if _, _, err := nativeTargetPath("copilot", "project", t.TempDir(), nativeArtifact{Kind: kind, Name: "nested/file.instructions.md"}); err == nil {
			t.Fatal("unrelated artifact registry widened")
		}
	}
	for _, isDir := range []bool{false, true} {
		repo, external := t.TempDir(), t.TempDir()
		writeFixture(t, filepath.Join(repo, ".github/instructions/nested/valid.instructions.md"), "Valid instructions.\n")
		target := external
		if !isDir {
			target = filepath.Join(external, "external.instructions.md")
			writeFixture(t, target, "External instructions.\n")
		}
		if err := os.Symlink(target, filepath.Join(repo, ".github/instructions/nested/link.instructions.md")); err != nil {
			t.Fatal(err)
		}
		before := nativeSkillTestSnapshot(t, repo)
		if err := ImportRepositoryWithOptions("copilot", repo, WriteOptions{Experimental: true, Force: true, Backup: true}); err == nil {
			t.Fatal("nested symlink imported")
		}
		after := nativeSkillTestSnapshot(t, repo)
		if len(before) != len(after) {
			t.Fatal("refusal wrote files")
		}
		for name, data := range before {
			if after[name] != data {
				t.Fatal("refusal changed source")
			}
		}
	}
}
