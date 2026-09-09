"""Original evidence scoped by a stored report citation or current QA fragment."""
import uuid
from fastapi import APIRouter, Depends, HTTPException, Response
from ...auth import Tenant, tenant_from_header
from ...features.evidence.original import original_view, read_original
from ...features.p3.contracts import IngestionError

router = APIRouter()


async def _respond(tenant: Tenant, response: Response, download: bool, **identity):
    response.headers['Cache-Control'] = 'no-store'
    try:
        if download:
            row, raw = await read_original(tenant, **identity)
            fmt = row['source_format']
            mime = {'pdf':'application/pdf','docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    'xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','jpeg':'image/jpeg'}[fmt]
            return Response(raw, media_type=mime, headers={'Cache-Control':'no-store',
                'Content-Disposition':f'attachment; filename="source.{fmt}"', 'X-Content-Type-Options':'nosniff'})
        return await original_view(tenant, **identity)
    except IngestionError as error:
        raise HTTPException(status_code=error.http_status, detail=error.code) from None
    except Exception:
        raise HTTPException(status_code=503, detail='CITATION_ORIGINAL_UNAVAILABLE') from None


@router.get('/citations/{citation_id}/original')
async def report_original(citation_id: uuid.UUID, response: Response, download: bool = False,
                          tenant: Tenant = Depends(tenant_from_header)):
    return await _respond(tenant, response, download, citation_id=citation_id)


@router.get('/versions/{version_id}/fragments/{fragment_id}/original')
async def qa_original(version_id: uuid.UUID, fragment_id: uuid.UUID, revision_id: uuid.UUID,
                      response: Response, download: bool = False, tenant: Tenant = Depends(tenant_from_header)):
    return await _respond(tenant, response, download, version_id=version_id, fragment_id=fragment_id, revision_id=revision_id)


@router.get('/versions/{version_id}/review-fragments/{fragment_id}/original')
async def review_original(version_id: uuid.UUID, fragment_id: uuid.UUID, revision_id: uuid.UUID,
                          response: Response, download: bool = False, tenant: Tenant = Depends(tenant_from_header)):
    return await _respond(tenant, response, download, version_id=version_id, fragment_id=fragment_id,
                          revision_id=revision_id, review_base=True)
