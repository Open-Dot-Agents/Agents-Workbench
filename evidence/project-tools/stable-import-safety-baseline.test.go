package config
import ("os";"path/filepath";"strings";"testing")
func TestStableCodexImportRejectsLiteralSecretsBeforeWrites(t *testing.T) {
	for name, native := range map[string]string{
		"literal-uri":    "[mcp_servers.demo]\ncommand = 'demo'\n[mcp_servers.demo.env]\nTOKEN = 'urn:open-dot-agents:env:TOKEN'\n",
		"literal-env":    "[mcp_servers.demo]\ncommand = 'demo'\n[mcp_servers.demo.env]\nTOKEN = 'oda-test-secret'\n",
		"mixed-env":      "[mcp_servers.demo]\ncommand = 'demo'\nenv_vars = ['PASSTHROUGH']\n[mcp_servers.demo.env]\nTOKEN = 'oda-test-secret'\n",
		"literal-header": "[mcp_servers.demo]\nurl = 'https://example.test/mcp'\n[mcp_servers.demo.http_headers]\nAuthorization = 'oda-test-secret'\n",
		"mixed-header":   "[mcp_servers.demo]\nurl = 'https://example.test/mcp'\n[mcp_servers.demo.http_headers]\nAuthorization = 'oda-test-secret'\n[mcp_servers.demo.env_http_headers]\nOther = 'PASSTHROUGH'\n",
	} {
		t.Run(name, func(t *testing.T) {
			root := t.TempDir()
			writeFixture(t, filepath.Join(root, "AGENTS.md"), "# Native instructions\n")
			writeFixture(t, filepath.Join(root, ".codex/config.toml"), native)
			before := instructionSnapshot(t, root)
			err := ImportRepositoryWithOptions("codex", root, WriteOptions{Force: true, Backup: true})
			if err == nil {
				t.Fatal("literal credential was accepted or discarded")
			}
			if strings.Contains(err.Error(), "oda-test-secret") {
				t.Fatal("diagnostic exposed a credential value")
			}
			if nativeHash(before) != nativeHash(instructionSnapshot(t, root)) {
				t.Fatal("refused import changed repository files")
			}
		})
	}
}


func TestStableImportValidatesRetainedProfilesBeforeWrites(t *testing.T) {
	root := t.TempDir()
	writeCanonicalFixture(t, root)
	writeFixture(t, filepath.Join(root, "AGENTS.md"), "# Native instructions\n")
	writeFixture(t, filepath.Join(root, ".codex/config.toml"), "[mcp_servers.demo]\ncommand = 'demo'\n")
	writeFixture(t, filepath.Join(root, ".agents/manifest.json"), `{"version":"1.0.0","profiles":["hooks"],"requires":["hooks.command"]}`)
	writeFixture(t, filepath.Join(root, ".agents/hooks/hooks.json"), `{"hooks":{"UnknownEvent":[]}}`)
	before := instructionSnapshot(t, root)
	if err := ImportRepositoryWithOptions("codex", root, WriteOptions{Force: true, Backup: true}); err == nil {
		t.Fatal("invalid retained profile was accepted or deactivated")
	}
	if nativeHash(before) != nativeHash(instructionSnapshot(t, root)) {
		t.Fatal("retained-profile validation failure changed files")
	}
}


func TestStableImportRefusesExternalInstructionLinkBeforeWrites(t *testing.T) {
	root := t.TempDir()
	outside := filepath.Join(t.TempDir(), "outside.md")
	writeFixture(t, outside, "oda-external-instruction-sentinel")
	writeFixture(t, filepath.Join(root, ".codex/config.toml"), "[mcp_servers.demo]\ncommand = 'demo'\n")
	if err := os.Symlink(outside, filepath.Join(root, "AGENTS.md")); err != nil {
		t.Fatal(err)
	}
	before := instructionSnapshot(t, root)
	if err := ImportRepositoryWithOptions("codex", root, WriteOptions{Force: true, Backup: true}); err == nil {
		t.Fatal("external instructions were imported")
	}
	if nativeHash(before) != nativeHash(instructionSnapshot(t, root)) {
		t.Fatal("instruction refusal changed files")
	}
}


func TestStableImportRefusesUnmappedMCPControlsBeforeWrites(t *testing.T) {
	for _, vendor := range []string{"codex", "copilot", "claude"} {
		for _, field := range []string{"enabled", "required", "disabled_tools", "bearer_token_env_var", "unknown_optional"} {
			t.Run(vendor+"/"+field, func(t *testing.T) {
				root := t.TempDir()
				writeFixture(t, filepath.Join(root, "AGENTS.md"), "# Native instructions\n")
				data := `{"mcpServers":{"demo":{"command":"demo","` + field + `":false}}}`
				if vendor == "codex" {
					data = "[mcp_servers.demo]\ncommand = 'demo'\n" + field + " = false\n"
				}
				writeFixture(t, vendorMCPPath(vendor, root), data)
				before := instructionSnapshot(t, root)
				err := ImportRepositoryWithOptions(vendor, root, WriteOptions{Force: true, Backup: true})
				if err == nil || !strings.Contains(err.Error(), field) {
					t.Fatalf("unmapped control not reported: %v", err)
				}
				if nativeHash(before) != nativeHash(instructionSnapshot(t, root)) {
					t.Fatal("unmapped control refusal changed files")
				}
			})
		}
	}
}


func TestStableImportPreservesRequiredCapabilities(t *testing.T) {
	root := t.TempDir()
	writeFixture(t, filepath.Join(root, "AGENTS.md"), "# Native instructions\n")
	writeFixture(t, filepath.Join(root, ".codex/config.toml"), "[mcp_servers.demo]\ncommand = 'demo'\nenv_vars = ['TOKEN']\n")
	writeCanonicalFixture(t, root)
	manifest := `{"version":"1.0.0","profiles":["tools","skills"],"requires":["mcp.envRef"]}`
	writeFixture(t, filepath.Join(root, ".agents/manifest.json"), manifest)
	if err := ImportRepositoryWithOptions("codex", root, WriteOptions{Force: true}); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(root, ".agents/manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(data), "mcp.envRef") {
		t.Fatal("forced import removed a required portable capability")
	}
}
