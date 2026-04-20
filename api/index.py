"""
Vercel Serverless Function - GET /api/
返回任务列表
"""
import sys
from pathlib import Path

# 确保项目根目录在 Python 路径中
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 导入共享的任务存储
from api.generate import TASKS


def handler(req, res):
    """Vercel Serverless Function 入口"""
    if req.method != "GET":
        return res.status(405).json({"error": "只支持 GET 请求"})

    # 构建任务列表（不包含 result 内容，减少响应体积）
    task_list = []
    for task_id, task in TASKS.items():
        task_list.append({
            "task_id": task["task_id"],
            "status": task["status"],
            "progress": task["progress"],
            "step": task["step"],
            "article_count": task.get("article_count", 0),
            "created_at": task.get("created_at", ""),
            "has_result": task["result"] is not None,
        })

    # 按创建时间倒序排列
    task_list.sort(key=lambda x: x["created_at"], reverse=True)

    return res.status(200).json({
        "success": True,
        "total": len(task_list),
        "tasks": task_list,
    })
