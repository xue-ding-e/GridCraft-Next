<!-- Modified for GridCraft-Next: distinguish historical upstream release guidance. -->

# Release playbook status

GridCraft-Next currently has no automatic release, signing or deployment workflow. See [the current release status](releasing.md). The following describes upstream tooling retained as historical engineering context, not an enabled release process for this independent project.

## Upstream reference


The release playbook shared by every crafting app lives in craftrules:
[`../craftrules/release/playbook.md`](../../../craftrules/release/playbook.md)
(`storytold/craftrules`, `release/playbook.md`). It is the canonical copy; don't duplicate it
here. The signing runbook is next to it in `release/signing-setup.md`.

GridCraft's copy was taken from DesignCraft (`.github/workflows/release.yml`, `packaging/`,
`cargo xtask version`). GridCraft-specific details are in [releasing.md](releasing.md).
