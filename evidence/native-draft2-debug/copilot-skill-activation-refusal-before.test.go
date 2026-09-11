package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestNativeCopilotSkillDiscoveryRefusal(t *testing.T) {
	for _, scope := range []string{"project", "user"} {
		for name, body := range map[string]string{
			"plain": "# Skill\nUse the fixture.\n",
			"yaml": "---\nname: fixture\nuser-invocable: [\n---\nUse the fixture.\n",
			"boolean": "---\nname: fixture\ndescription: Fixture.\nuser-invocable: \"false\"\n---\nUse the fixture.\n",
		} {
			t.Run(scope+"/"+name, func(t *testing.T) {
				t.Setenv("XDG_STATE_HOME", t.TempDir())
				repo, home := t.TempDir(), t.TempDir()
				writeFixture(t, filepath.Join(repo, ".agents/AGENTS.md"), "Use fixture data.\n")
				writeFixture(t, filepath.Join(repo, ".agents/manifest.json"), `{"version":"1.1.0-draft.2","profiles":["skills"]}`)
				path := filepath.Join(repo, ".agents/skills/fixture/SKILL.md")
				writeFixture(t, path, body)
				options := ApplyOptions{Experimental: true, Scope: scope, Force: true}
				if scope == "user" { options.NativeHome = home }
				if err := ValidateRepositoryWithOptions(filepath.Join(repo, ".agents"), true); err != nil { t.Fatal(err) }
				plan, err := PlanProjection("copilot", repo, options)
				if err != nil { t.Fatal(err) }
				if plan.Applicable || len(plan.Actions) != 0 || len(plan.Diagnostics) == 0 {
					t.Fatalf("ignored native skill was offered as applicable: %+v", plan)
				}
				if _, err := ApplyProjection("copilot", repo, options); err == nil { t.Fatal("ignored native skill applied") }
				if readNativeTest(t, path) != body { t.Fatal("source changed") }
				if _, err := os.Stat(filepath.Join(home, "skills")); !os.IsNotExist(err) { t.Fatal("refusal wrote user skills") }
			})
		}
	}
}
