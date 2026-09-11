package config

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
)

func TestNativeCopilotAgentInstructionFiles(t *testing.T) {
	source, target := t.TempDir(), t.TempDir()
	definitions := map[string]string{
		"AGENTS.md": "Root policy.\n@policy.md\n",
		"CLAUDE.md": "Other agent policy.\n@policy.md\n",
		".claude/CLAUDE.md": "Directory policy.\n@policy.md\n",
		"GEMINI.md": "Keep literal @policy.md here.\n",
	}
	writeFixture(t, filepath.Join(source, ".github/copilot-instructions.md"), "Portable policy.\n")
	for name, body := range definitions { writeFixture(t, filepath.Join(source, name), body) }
	writeFixture(t, filepath.Join(source, "policy.md"), "External referenced content.\n")
	if err := ImportRepositoryWithOptions("copilot", source, WriteOptions{Experimental: true}); err != nil { t.Fatal(err) }
	for name, body := range definitions {
		if readNativeTest(t, filepath.Join(source, name)) != body || readNativeTest(t, filepath.Join(source, ".agents/native/com.github.copilot/agent-instructions", name)) != body { t.Fatal("native instruction bytes lost", name) }
	}
	if _, err := os.Stat(filepath.Join(source, ".agents/native/com.github.copilot/policy.md")); !os.IsNotExist(err) { t.Fatal("external reference copied", err) }
	if err := os.CopyFS(filepath.Join(target, ".agents"), os.DirFS(filepath.Join(source, ".agents"))); err != nil { t.Fatal(err) }
	options := ApplyOptions{Experimental: true}
	if _, err := ApplyProjection("copilot", target, options); err != nil { t.Fatal(err) }
	for name, body := range definitions { if readNativeTest(t, filepath.Join(target, name)) != body { t.Fatal("native location changed", name) } }
	if err := ImportRepositoryWithOptions("copilot", target, WriteOptions{Experimental: true}); err != nil { t.Fatal(err) }
	before := instructionSnapshot(t, target)
	if err := ImportRepositoryWithOptions("copilot", target, WriteOptions{Experimental: true}); err != nil { t.Fatal(err) }
	if nativeHash(before) != nativeHash(instructionSnapshot(t, target)) { t.Fatal("reimport changed files") }
	profilePath := filepath.Join(target, ".agents/native/com.github.copilot/profile.json")
	var profile nativeProfile
	if err := nativeDecodePolicy(profilePath, &profile); err != nil { t.Fatal(err) }
	profile.Artifacts = []nativeArtifact{}
	data, err := json.Marshal(profile)
	if err != nil { t.Fatal(err) }
	writeFixture(t, profilePath, string(data))
	options.Backup = true
	build, err := buildNativeProjection("copilot", target, options)
	if err != nil || !build.plan.Applicable { t.Fatal(build.plan, err) }
	before = instructionSnapshot(t, target)
	writes := 0
	err = nativeRunTransaction(build.changes, func(stage string, index int) error {
		if stage == "after-write" {
			writes++
			if index == len(build.changes)-1 { return fmt.Errorf("injected final instruction removal failure") }
		}
		return nil
	})
	if err == nil || writes != len(build.changes) { t.Fatal("rollback injection failed", err) }
	if nativeHash(before) != nativeHash(instructionSnapshot(t, target)) { t.Fatal("rollback lost native instructions or backups") }
	if _, err = ApplyProjection("copilot", target, options); err != nil { t.Fatal(err) }
	for name, body := range definitions {
		if _, err := os.Stat(filepath.Join(target, name)); !os.IsNotExist(err) { t.Fatal("deselected native instructions remain", name, err) }
		if readNativeTest(t, filepath.Join(target, name+".bak")) != body { t.Fatal("backup content lost", name) }
	}
}

func TestNativeAgentInstructionRegistryScope(t *testing.T) {
	for _, name := range []string{"AGENTS.md", "CLAUDE.md", ".claude/CLAUDE.md", "GEMINI.md"} {
		if _, _, err := nativeTargetPath("copilot", "project", t.TempDir(), nativeArtifact{Kind: "agent-instructions", Name: name}); err != nil { t.Fatal(err) }
		for _, vendor := range []string{"codex", "copilot"} {
			if _, _, err := nativeTargetPath(vendor, "user", t.TempDir(), nativeArtifact{Kind: "agent-instructions", Name: name}); err == nil { t.Fatal("agent instruction mapping escaped project scope") }
		}
	}
	for _, name := range []string{"../AGENTS.md", "/AGENTS.md", "other/AGENTS.md", ".git/config", ".github/copilot-instructions.md"} {
		if _, _, err := nativeTargetPath("copilot", "project", t.TempDir(), nativeArtifact{Kind: "agent-instructions", Name: name}); err == nil { t.Fatal("arbitrary output path accepted", name) }
	}
}
