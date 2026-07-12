"""Tests de la découverte locale — simule un ~/Projets avec plusieurs repos."""

from gha_audit.discovery.local_discovery import find_workflow_files, load_local_sources


def _make_repo(root, name, workflow_files):
    repo = root / name
    workflows_dir = repo / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    for filename, content in workflow_files.items():
        (workflows_dir / filename).write_text(content, encoding="utf-8")
    return repo


def test_finds_workflows_across_multiple_sibling_repos(tmp_path):
    _make_repo(tmp_path, "stormgrill", {"ci.yml": "name: CI"})
    _make_repo(tmp_path, "prompt-grill-framework", {"ci.yml": "name: CI", "release.yaml": "name: Release"})

    found = find_workflow_files(tmp_path)
    names = {p.name for p in found}
    assert names == {"ci.yml", "release.yaml"}
    assert len(found) == 3  # ci.yml apparaît dans les deux repos


def test_finds_single_repo_directly(tmp_path):
    repo = _make_repo(tmp_path, "solo-repo", {"ci.yml": "name: CI"})
    found = find_workflow_files(repo)
    assert len(found) == 1
    assert found[0].name == "ci.yml"


def test_ignores_non_workflow_files(tmp_path):
    repo = _make_repo(tmp_path, "repo", {"ci.yml": "name: CI"})
    (repo / ".github" / "workflows" / "README.md").write_text("not a workflow", encoding="utf-8")
    found = find_workflow_files(repo)
    assert len(found) == 1


def test_missing_root_returns_empty_list(tmp_path):
    assert find_workflow_files(tmp_path / "does-not-exist") == []


def test_repo_without_workflows_dir_returns_empty(tmp_path):
    (tmp_path / "empty-repo").mkdir()
    assert find_workflow_files(tmp_path / "empty-repo") == []


def test_load_local_sources_sets_content_and_writable_flag(tmp_path):
    _make_repo(tmp_path, "myrepo", {"ci.yml": "name: CI\non: push"})
    sources = load_local_sources(tmp_path)
    assert len(sources) == 1
    source = sources[0]
    assert source.content == "name: CI\non: push"
    assert source.is_writable is True
    assert source.repo_slug == "myrepo"


def test_load_local_sources_repo_slug_derived_from_parent_dir(tmp_path):
    _make_repo(tmp_path, "stormgrill", {"ci.yml": "name: CI"})
    _make_repo(tmp_path, "prompt-grill-framework", {"ci.yml": "name: CI"})

    sources = load_local_sources(tmp_path)
    slugs = {s.repo_slug for s in sources}
    assert slugs == {"stormgrill", "prompt-grill-framework"}
