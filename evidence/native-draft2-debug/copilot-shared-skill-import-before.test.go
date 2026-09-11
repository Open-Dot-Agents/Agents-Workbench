package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestNativeCopilotSharedSkillImport(t *testing.T) {
	for _, manifest := range []bool{false, true} {
		t.Run(map[bool]string{false: "bare", true: "draft2"}[manifest], func(t *testing.T) {
			repo := t.TempDir()
			root := filepath.Join(repo, ".agents")
			definition := filepath.Join(root, "skills/fixture/SKILL.md")
			writeFixture(t, definition, importedSkill)
			writeFixture(t, filepath.Join(root, "skills/fixture/data.bin"), "\x00\xff")
			if manifest {
				writeFixture(t, filepath.Join(root, "manifest.json"), `{"version":"1.1.0-draft.2","profiles":[]}`)
				writeFixture(t, filepath.Join(root, "AGENTS.md"), "Keep canonical policy.\n")
			}
			info, err := os.Stat(definition)
			if err != nil { t.Fatal(err) }
			options := WriteOptions{Experimental: true}
			if err := ImportRepositoryWithOptions("copilot", repo, options); err != nil { t.Fatal(err) }
			if !strings.Contains(readNativeTest(t, filepath.Join(root, "manifest.json")), `"skills"`) { t.Fatal("shared skills were not selected") }
			if err := ValidateRepositoryWithOptions(root, true); err != nil { t.Fatal(err) }
			if err := ImportRepositoryWithOptions("copilot", repo, options); err != nil { t.Fatal(err) }
			after, err := os.Stat(definition)
			if err != nil || !os.SameFile(info, after) || info.Mode() != after.Mode() { t.Fatal("import replaced the shared source") }
			if readNativeTest(t, definition) != importedSkill || readNativeTest(t, filepath.Join(root, "skills/fixture/data.bin")) != "\x00\xff" { t.Fatal("shared package bytes changed") }
			if manifest && readNativeTest(t, filepath.Join(root, "AGENTS.md")) != "Keep canonical policy.\n" { t.Fatal("canonical policy changed") }
			plan, err := PlanProjection("copilot", repo, ApplyOptions{Experimental: true})
			if err != nil || !plan.Applicable { t.Fatalf("shared skill plan: %v %+v", err, plan) }
		})
	}
}
