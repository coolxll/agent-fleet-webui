from unittest.mock import patch
from agent_fleet.services import github_board_service


def test_extract_linked_issues():
    assert github_board_service.extract_linked_issues("feat(ci): Closes #12, Fixes #13 and Refs #9") == [9, 12, 13]
    assert github_board_service.extract_linked_issues("branch feat/issue-15-architecture") == [15]
    assert github_board_service.extract_linked_issues("fix/issue/7-bug") == [7]
    assert github_board_service.extract_linked_issues("No issues here") == []


def test_fetch_github_board_mocked(client):
    mock_issues = [
        {"number": 1, "title": "Issue 1", "state": "OPEN", "labels": [], "url": "http://gh/1"},
        {"number": 2, "title": "Issue 2", "state": "CLOSED", "labels": [], "url": "http://gh/2"},
    ]
    mock_prs = [
        {"number": 10, "title": "PR for issue 1", "state": "OPEN", "headRefName": "feat/issue-1", "url": "http://gh/pr/10"},
    ]
    mock_actions = [
        {"databaseId": 1234, "name": "CI", "status": "completed", "conclusion": "success", "headBranch": "feat/issue-1", "url": "http://gh/actions/1234"},
    ]

    def mock_run_gh(args, **kwargs):
        if "issue" in args:
            return mock_issues
        if "pr" in args:
            return mock_prs
        if "run" in args:
            return mock_actions
        return {"nameWithOwner": "coolxll/test-repo"}

    with patch("agent_fleet.services.github_board_service._run_gh_json", side_effect=mock_run_gh):
        board = github_board_service.fetch_github_board(repo="coolxll/test-repo")
        assert board["ok"] is True
        assert board["repo"] == "coolxll/test-repo"
        assert len(board["issues"]) == 2
        assert len(board["pull_requests"]) == 1
        assert len(board["actions"]) == 1

        # Check Issue 1 has linked PR 10 and linked CI action
        iss1 = next(i for i in board["issues"] if i["number"] == 1)
        assert len(iss1["linked_prs"]) == 1
        assert iss1["linked_prs"][0]["number"] == 10
        assert iss1["latest_action"]["conclusion"] == "success"

        # Check API route
        resp = client.get("/api/tasks/github/board?repo=coolxll/test-repo")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["counts"]["open_issues"] == 1
