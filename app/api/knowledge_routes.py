"""故障知识库 API 路由"""


import logging

from fastapi import APIRouter, HTTPException, UploadFile

from app.services.knowledge_base import knowledge_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])

ALLOWED_SUFFIXES = (".md", ".txt", ".markdown")


@router.get("/template")
async def get_template():
    """获取知识文件上传模板（供前端下载 / 预览）"""
    return knowledge_service.get_template()


@router.post("/upload")
async def upload_document(file: UploadFile):
    """上传知识文档（markdown / txt），解析为案例并入库"""
    name = file.filename or "unnamed.md"
    if not name.lower().endswith(ALLOWED_SUFFIXES):
        raise HTTPException(
            status_code=400,
            detail=f"仅支持 {' / '.join(ALLOWED_SUFFIXES)} 格式",
        )
    content = (await file.read()).decode("utf-8", errors="replace")
    if not content.strip():
        raise HTTPException(status_code=400, detail="文件内容为空")

    try:
        result = await knowledge_service.upload_document(name, content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    logger.info("Knowledge doc uploaded: %s (%d cases)", name, result["case_count"])
    return result


@router.get("/documents")
async def list_documents():
    """知识库文档列表"""
    return knowledge_service.list_documents()


@router.get("/documents/{doc_id}/content")
async def get_document_content(doc_id: str):
    """获取文档内容（按原格式重建，供在线预览 / 下载）"""
    doc = knowledge_service.get_document_content(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    return doc


@router.get("/stats")
async def stats():
    """知识库统计信息"""
    return knowledge_service.stats()


@router.get("/search")
async def search(q: str, top_k: int = 3):
    """测试检索：输入告警特征文本，返回最相似故障案例"""
    if not q.strip():
        return {"query": q, "cases": [], "total": 0}
    cases = knowledge_service.query(q, top_k=top_k)
    return {"query": q, "cases": cases, "total": len(cases)}


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str):
    """删除知识库中的一份文档"""
    await knowledge_service.delete_document(doc_id)
    return {"deleted": doc_id}
