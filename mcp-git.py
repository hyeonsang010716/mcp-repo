"""
──────────────────────────────────────────────────────────────────────────────
mcp-git.py

✔️  기능 요약
──────────────────────────────────────────────────────────────────────────────
1.  로컬 폴더(예: week1/)를 순회해 GitHub MCP `push_files` 형식으로 변환
2.  GitHub MCP 서버(docker image)와 stdio transport 로 연결
3.  ┌ 새 브랜치 create_branch    (없으면 생성, 있으면 패스)
    ├ 여러 파일   push_files      (텍스트 / 바이너리 자동 처리)
    └ Pull Request create_pull_request
4.  모든 과정이 **async** 로 동작하므로 FastAPI 백엔드·CLI 스크립트 어디서든 await 가능
──────────────────────────────────────────────────────────────────────────────
"""

from pathlib import Path, PurePosixPath
import base64, mimetypes
from langchain_mcp_adapters.client import MultiServerMCPClient


# ────────────────────────────────────────────────────────────────────────────
# 1)  업로드 할 파일 셋업
# ────────────────────────────────────────────────────────────────────────────
def mCall_CollectFiles(
    root: Path,
    remote_prefix: str = "my_dir_name/",
):
    """
    Parameters
    ----------
    root : Path
        업로드할 **로컬** 기준 폴더(Path 객체).
        e.g.  Path("week1")  →  week1/* 전체 재귀 순회
    remote_prefix : str
        GitHub 저장소 **원격** 경로 앞부분.
        e.g.  "hyeonsang/"  →  hyeonsang/<원본 구조> 로 푸시됨.

    Returns
    -------
    List[dict]
        MCP `repos.push_files` 툴이 요구하는
        { "path": str, "content": str, "encoding": "base64"? } 목록.
    """
    files = []

    for p in root.rglob("*"):              # 하위 전부 재귀 순회
        if not p.is_file():                # 디렉터리는 건너뜀
            continue

        # ── ① 리모트에 올라갈 경로
        remote_path = str(PurePosixPath(remote_prefix) / p.relative_to(root))

        # ── ② 파일 바이트 읽기
        raw = p.read_bytes()

        # ── ③ 텍스트 vs 바이너리 판별
        mime, _ = mimetypes.guess_type(p.name)
        try:
            if mime and mime.startswith("text/"):           # text/* MIME
                files.append({"path": remote_path,
                              "content": raw.decode("utf-8")})
                continue
        except UnicodeDecodeError:
            pass  # UTF‑8 디코딩 실패 → 바이너리 처리

        # ── ④ 바이너리: base64 + encoding 지정
        files.append({
            "path": remote_path,
            "content": base64.b64encode(raw).decode(),
            "encoding": "base64"
        })

    return files


# ────────────────────────────────────────────────────────────────────────────
# 2)  브랜치 생성 → 파일 Push → Pull Request
# ────────────────────────────────────────────────────────────────────────────
async def rCall_PushAndPR(
    my_dir_name: str,      # 리모트 기준 상위 폴더 (예: "hyeonsang/")
    dir_name: str,         # 로컬 업로드 대상 폴더   (예: "week1")
    branch_name: str,      # 새로 만들 브랜치 이름   (예: "feat/hyeonsang-week1")
    pr_title: str,         # PR 제목
    pr_body: str,          # PR 본문 (Markdown 가능)
    git_token: str,        # GitHub Personal Access Token
) -> None:
    """
    SGCS‑Release‑Git‑Project/release-collect-game 저장소에
    ① 브랜치를 만들고
    ② dir_name 폴더를 my_dir_name/ 하위로 push 한 뒤
    ③ Pull Request 를 여는 올‑인‑원 async 함수
    """
    # ── 1. 파일 목록 변환 ───────────────────────────────────────
    files = mCall_CollectFiles(Path(dir_name), my_dir_name)

    # ── 2. MCP 서버(stdio) 연결 ────────────────────────────────
    async with MultiServerMCPClient({
        "github": {                                  # MCP 서버 식별자
            "command": "docker",                     # 컨테이너 안에서 또 docker run
            "args": [
                "run", "-i", "--rm",
                "-e", "GITHUB_PERSONAL_ACCESS_TOKEN",# 토큰 env 전달
                "ghcr.io/github/github-mcp-server"   # 공식 이미지
            ],
            "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": git_token},
            "transport": "stdio"                     # stdio ↔ subprocess 연결
        }
    }) as client:

        # ── 3. 사용 가능한 MCP 툴 매핑 ───────────────────────
        tool_map = {t.name: t for t in client.get_tools()}
        create_branch       = tool_map["create_branch"]
        push_files_tool     = tool_map["push_files"]
        create_pull_request = tool_map["create_pull_request"]

        # ── 4. 브랜치 생성 (존재 시 건너뜀) ───────────────────
        try:
            await create_branch.ainvoke({
                "owner": "SGCS-Release-Git-Project",
                "repo":  "release-collect-game",
                "branch": branch_name,
                "from_branch": "main"          # 기준 브랜치
            })
            print(f"✅ 브랜치 '{branch_name}' 생성")
        except Exception:
            print(f"ℹ️  '{branch_name}' 브랜치 이미 존재 — 계속 진행")

        # ── 5. 파일 Push ────────────────────────────────────
        await push_files_tool.ainvoke({
            "owner":  "SGCS-Release-Git-Project",
            "repo":   "release-collect-game",
            "branch": branch_name,
            "files":  files,
            "message": pr_title              # 커밋 메시지
        })

        # ── 6. Pull Request 생성 ────────────────────────────
        await create_pull_request.ainvoke({
            "owner":  "SGCS-Release-Git-Project",
            "repo":   "release-collect-game",
            "title":  pr_title,
            "body":   pr_body,
            "head":   branch_name,   # 내 브랜치
            "base":   "main"         # 병합 대상
        })

        print("🎉  PR 생성 완료!")
    return None
    # 모든 과정이 성공하면 None 반환
