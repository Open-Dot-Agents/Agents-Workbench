package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func canonicalInstructionImportFixture(t *testing.T) string {
	t.Helper()
	repo := t.TempDir()
	writeFixture(t, filepath.Join(repo, ".agents/manifest.json"), `{"version":"1.1.0-draft.2","profiles":[]}`)
	writeFixture(t, filepath.Join(repo, ".agents/AGENTS.md"), "Core policy.\n@policy.md\n")
	if err := os.Symlink(".agents/AGENTS.md", filepath.Join(repo, "AGENTS.md")); err != nil { t.Fatal(err) }
	writeFixture(t, filepath.Join(repo, ".github/copilot-instructions.md"), "Native policy.\n@native-policy.md\n")
	return repo
}

func TestNativeCanonicalInstructionImportRelocation(t *testing.T) {
	source, target := canonicalInstructionImportFixture(t), t.TempDir()
	core := readNativeTest(t, filepath.Join(source, ".agents/AGENTS.md"))
	native := readNativeTest(t, filepath.Join(source, ".github/copilot-instructions.md"))
	if err := ImportRepositoryWithOptions("copilot", source, WriteOptions{Experimental: true}); err != nil { t.Fatal(err) }
	if readNativeTest(t, filepath.Join(source, ".agents/AGENTS.md")) != core { t.Fatal("native body replaced core") }
	if readNativeTest(t, filepath.Join(source, ".agents/native/com.github.copilot/copilot-instructions.md")) != native { t.Fatal("native body lost") }
	if err := os.CopyFS(filepath.Join(target, ".agents"), os.DirFS(filepath.Join(source, ".agents"))); err != nil { t.Fatal(err) }
	if _, err := ApplyProjection("copilot", source, ApplyOptions{Experimental: true, Adopt: true}); err != nil { t.Fatal(err) }
	if link, err := os.Readlink(filepath.Join(source, "AGENTS.md")); err != nil || link != ".agents/AGENTS.md" { t.Fatal("source link changed", err) }
	if _, err := ApplyProjection("copilot", target, ApplyOptions{Experimental: true}); err != nil { t.Fatal(err) }
	if readNativeTest(t, filepath.Join(target, "AGENTS.md")) != core || readNativeTest(t, filepath.Join(target, ".github/copilot-instructions.md")) != native { t.Fatal("relocation lost an instruction body") }
	if err := ImportRepositoryWithOptions("copilot", target, WriteOptions{Experimental: true}); err != nil { t.Fatal(err) }
	before := instructionSnapshot(t, target)
	if err := ImportRepositoryWithOptions("copilot", target, WriteOptions{Experimental: true}); err != nil { t.Fatal(err) }
	if nativeHash(before) != nativeHash(instructionSnapshot(t, target)) { t.Fatal("reimport changed canonical data") }
	writeFixture(t, filepath.Join(target, ".agents/AGENTS.md"), "Updated core policy.\n@policy.md\n")
	if _, err := ApplyProjection("copilot", target, ApplyOptions{Experimental: true}); err != nil { t.Fatal(err) }
	if readNativeTest(t, filepath.Join(target, "AGENTS.md")) != "Updated core policy.\n@policy.md\n" || readNativeTest(t, filepath.Join(target, ".github/copilot-instructions.md")) != native { t.Fatal("core update changed the wrong body") }
	writeFixture(t, filepath.Join(target, "AGENTS.md"), "Conflicting native edit.\n")
	before = instructionSnapshot(t, target)
	if err := ImportRepositoryWithOptions("copilot", target, WriteOptions{Experimental: true, Force: true, Backup: true}); err == nil { t.Fatal("force replaced canonical core") }
	if nativeHash(before) != nativeHash(instructionSnapshot(t, target)) { t.Fatal("conflict changed files") }
}

func TestNativeCanonicalInstructionBindingValidation(t *testing.T) {
	for _, source := range []string{"other.md", "../AGENTS.md", "native/AGENTS.md"} {
		repo := canonicalInstructionImportFixture(t)
		dir := filepath.Join(repo, ".agents/native/com.github.copilot")
		writeFixture(t, filepath.Join(repo, ".agents/manifest.json"), `{"version":"1.1.0-draft.2","profiles":["native"]}`)
		p := nativeProfile{Namespace: "com.github.copilot", HarnessVersion: "=1.0.83", Scope: "project", Required: true,
			Artifacts: []nativeArtifact{{Kind: "canonical-instructions", Source: source}}}
		data, err := json.Marshal(p)
		if err != nil { t.Fatal(err) }
		writeFixture(t, filepath.Join(dir, "profile.json"), string(data))
		if source == "other.md" { writeFixture(t, filepath.Join(dir, source), "Namespace copy must not replace core.\n") }
		before := instructionSnapshot(t, repo)
		if _, err := ApplyProjection("copilot", repo, ApplyOptions{Experimental: true, Force: true, Adopt: true}); err == nil { t.Fatal("noncanonical source accepted", source) }
		if nativeHash(before) != nativeHash(instructionSnapshot(t, repo)) { t.Fatal("invalid binding changed files") }
	}
	for _, scope := range []string{"project", "user"} {
		for _, vendor := range []string{"codex", "copilot"} {
			_, _, err := nativeTargetPath(vendor, scope, t.TempDir(), nativeArtifact{Kind: "canonical-instructions", Source: "AGENTS.md"})
			if (err == nil) != (vendor == "copilot" && scope == "project") { t.Fatal(vendor, scope, err) }
		}
	}
}

func TestNativeCanonicalInstructionDuplicateBinding(t *testing.T) {
	repo := canonicalInstructionImportFixture(t)
	if err := ImportRepositoryWithOptions("copilot", repo, WriteOptions{Experimental: true}); err != nil { t.Fatal(err) }
	path := filepath.Join(repo, ".agents/native/com.github.copilot/profile.json")
	var p nativeProfile
	if err := nativeDecodePolicy(path, &p); err != nil { t.Fatal(err) }
	p.Artifacts = append(p.Artifacts, nativeArtifact{Kind: "canonical-instructions", Source: "AGENTS.md"})
	data, err := json.Marshal(p)
	if err != nil { t.Fatal(err) }
	writeFixture(t, path, string(data))
	before := instructionSnapshot(t, repo)
	if _, err := ApplyProjection("copilot", repo, ApplyOptions{Experimental: true}); err == nil || !strings.Contains(err.Error(), "duplicate") { t.Fatal("duplicate core binding accepted", err) }
	if nativeHash(before) != nativeHash(instructionSnapshot(t, repo)) { t.Fatal("duplicate binding changed files") }
}
