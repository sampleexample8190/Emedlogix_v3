from fastapi import APIRouter, HTTPException, Depends
from app.models.provider import OCRPayload, AutoFillResponse
from app.services.matching import MatchingEngine

router = APIRouter()

async def get_matching_engine():
    return MatchingEngine()

@router.post("/provider/auto-fill", response_model=AutoFillResponse)
async def auto_fill_provider(
    payload: dict, # Accepting dict to handle nested "ocr_payload" key broadly or specific model
    engine: MatchingEngine = Depends(get_matching_engine)
):
    # User spec: Input { "ocr_payload": { ... } }
    if "ocr_payload" not in payload:
        raise HTTPException(status_code=400, detail="Missing 'ocr_payload' key")
    
    ocr_data_dict = payload["ocr_payload"]
    ocr_data = OCRPayload(**ocr_data_dict)
    
    result = await engine.process_ocr_data(ocr_data)
    return result
