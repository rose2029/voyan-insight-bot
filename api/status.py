"""
Vercel Serverless Function - GET /api/status?task_id=xxx
查询任务进度
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

    task_id = req.query.get("task_id", "")

    if not task_id:
        return res.status(400).json({
            "success": False,
            "error": "缺少 task_id 参数",
        })

    if task_id not in TASKS:
        return res.status(404).json({
            "success": False,
            "error": f"任务 {task_id} 不存在",
        })

    task = TASKS[task_id]

    return res.status(200).json({
        "success": True,
        "task_id": task["task_id"],
        "status": task["status"],
        "progress": task["progress"],
        "step": task["step"],
        "article_count": task.get("article_count", 0),
        "error": task.get("error"),
        "has_result": task["result"] is not None,
    })
