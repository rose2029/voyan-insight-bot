"""
Vercel Serverless Function - GET /api/download?task_id=xxx&format=md
下载日报内容
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
    fmt = req.query.get("format", "md")

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

    if task["status"] != "done":
        return res.status(400).json({
            "success": False,
            "error": f"任务尚未完成，当前状态: {task['status']}",
            "status": task["status"],
            "progress": task["progress"],
        })

    if not task["result"]:
        return res.status(404).json({
            "success": False,
            "error": "日报内容为空",
        })

    if fmt == "md":
        # 返回 Markdown 文件
        filename = f"洞察信息收集日报_{task_id[:8]}.md"
        res.setHeader("Content-Type", "text/markdown; charset=utf-8")
        res.setHeader("Content-Disposition", f'attachment; filename="{filename}"')
        return res.status(200).send(task["result"])
    elif fmt == "json":
        # 返回 JSON 格式
        res.setHeader("Content-Type", "application/json; charset=utf-8")
        return res.status(200).json({
            "success": True,
            "task_id": task_id,
            "format": "json",
            "content": task["result"],
            "article_count": task.get("article_count", 0),
        })
    else:
        return res.status(400).json({
            "success": False,
            "error": f"不支持的格式: {fmt}，仅支持 md 和 json",
        })
