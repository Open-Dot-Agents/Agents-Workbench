package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const importedSkill = "---\nname: fixture\ndescription: Fixture.\n---\nUse the fixture.\n"

func TestNativeCopilotProjectSkillImport(t *testing.T) {
	for _, origin := range []string{".github", ".claude"} {
		t.Run(origin, func(t *testing.T) {
			repo := t.TempDir()
			packageRoot := filepath.Join(repo, origin, "skills", "fixture")
			writeFixture(t, filepath.Join(packageRoot, "SKILL.md"), importedSkill)
			writeFixture(t, filepath.Join(packageRoot, "scripts", "probe.sh"), "#!/bin/sh\nexit 0\n")
			if err := os.Chmod(filepath.Join(packageRoot, "scripts", "probe.sh"), 0700); err != nil { t.Fatal(err) }
			options := WriteOptions{Experimental: true}
			if err := ImportRepositoryWithOptions("copilot", repo, options); err != nil { t.Fatal(err) }
			if readNativeTest(t, filepath.Join(repo, ".agents/skills/fixture/SKILL.md")) != importedSkill { t.Fatal("definition not imported") }
			if info, err := os.Stat(filepath.Join(repo, ".agents/skills/fixture/scripts/probe.sh")); err != nil || info.Mode().Perm() != 0700 { t.Fatal("executable asset lost") }
			if !strings.Contains(readNativeTest(t, filepath.Join(repo, ".agents/manifest.json")), `"skills"`) { t.Fatal("profile not selected") }
			if err := ValidateRepositoryWithOptions(filepath.Join(repo, ".agents"), true); err != nil { t.Fatal(err) }
			if err := ImportRepositoryWithOptions("copilot", repo, options); err != nil { t.Fatalf("repeat import: %v", err) }
		})
	}
}

func TestNativeCopilotProjectSkillImportConflicts(t *testing.T) {
	for _, scenario := range []string{"different-definition", "extra-asset", "different-folder", "canonical-extra", "symlink"} {
		t.Run(scenario, func(t *testing.T) {
			repo := t.TempDir()
			first := filepath.Join(repo, ".github/skills/fixture")
			second := filepath.Join(repo, ".claude/skills/fixture")
			if scenario == "different-folder" { second = filepath.Join(repo, ".claude/skills/other") }
			if scenario == "canonical-extra" {
				second = filepath.Join(repo, ".agents/skills/fixture")
				writeFixture(t, filepath.Join(repo, ".agents/AGENTS.md"), "Keep canonical policy.\n")
				writeFixture(t, filepath.Join(repo, ".agents/manifest.json"), `{"version":"1.1.0-draft.2","profiles":["skills"]}`)
			}
			writeFixture(t, filepath.Join(first, "SKILL.md"), importedSkill)
			writeFixture(t, filepath.Join(second, "SKILL.md"), importedSkill)
			switch scenario {
			case "different-definition": writeFixture(t, filepath.Join(second, "SKILL.md"), importedSkill+"Different body.\n")
			case "extra-asset", "canonical-extra": writeFixture(t, filepath.Join(second, "extra.txt"), "Do not combine packages.\n")
			case "symlink":
				if err := os.Symlink(filepath.Join(first, "SKILL.md"), filepath.Join(second, "link.md")); err != nil { t.Fatal(err) }
			}
			before := nativeSkillTestSnapshot(t, repo)
			if err := ImportRepositoryWithOptions("copilot", repo, WriteOptions{Experimental: true, Force: true, Backup: true}); err == nil { t.Fatal("ambiguous project skills imported") }
			after := nativeSkillTestSnapshot(t, repo)
			if len(after) != len(before) { t.Fatal("refusal wrote files") }
			for name, content := range before { if after[name] != content { t.Fatalf("refusal changed %s", name) } }
		})
	}
}

func nativeSkillTestSnapshot(t *testing.T, root string) map[string]string {
	t.Helper()
	files := map[string]string{}
	if err := filepath.WalkDir(root, func(path string, entry os.DirEntry, err error) error {
		if err != nil { return err }
		if entry.IsDir() || entry.Name() == ".agents-import.lock" { return nil }
		if entry.Type()&os.ModeSymlink != 0 { target, err := os.Readlink(path); files[path] = "link:"+target; return err }
		data, err := os.ReadFile(path); files[path] = string(data); return err
	}); err != nil { t.Fatal(err) }
	return files
}
