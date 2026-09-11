package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestNativeCopilotUserInstructionsKeepDeclaredSource(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", t.TempDir())
	repo, home := t.TempDir(), t.TempDir()
	root := filepath.Join(repo, ".agents")
	writeFixture(t, filepath.Join(root, "manifest.json"), `{"version":"1.1.0-draft.2","profiles":["native"]}`)
	writeFixture(t, filepath.Join(root, "AGENTS.md"), "Keep project policy.\n")
	profile := filepath.Join(root, "native/com.github.copilot/profile.json")
	writeFixture(t, profile, `{"namespace":"com.github.copilot","harness_version":"=1.0.83","scope":"user","required":true,"artifacts":[{"kind":"instructions","source":"policy/local.md"}]}`)
	source := filepath.Join(root, "native/com.github.copilot/policy/local.md")
	writeFixture(t, source, "User policy.\n@policy.md\n")
	writeFixture(t, filepath.Join(home, "policy.md"), "External user reference.\n")
	options := ApplyOptions{Experimental:true, Scope:"user", NativeHome:home}
	if _, err := ApplyProjection("copilot", repo, options); err != nil { t.Fatal(err) }
	before := instructionSnapshot(t, repo)
	if err := ImportRepositoryWithOptions("copilot", repo, WriteOptions{Experimental:true, Scope:"user", NativeHome:home}); err != nil { t.Fatal(err) }
	if nativeHash(before) != nativeHash(instructionSnapshot(t, repo)) { t.Fatal("reimport changed the declared instruction source or profile") }
	if _, err := os.Stat(filepath.Join(root,"native/com.github.copilot/copilot-instructions.md")); !os.IsNotExist(err) { t.Fatal("reimport created a second instruction source") }
	plan, err := PlanProjection("copilot", repo, options)
	if err != nil || !plan.Applicable { t.Fatalf("reimported plan: %v %+v",err,plan) }
}
