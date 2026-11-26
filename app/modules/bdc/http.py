from fastapi import HTTPException
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute


class BdcRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def guarded(request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                response = await request_validation_exception_handler(request, exc)
                response.headers["Cache-Control"] = "no-store"
                return response
            except HTTPException as exc:
                exc.headers = {**(exc.headers or {}), "Cache-Control": "no-store"}
                raise

        return guarded
