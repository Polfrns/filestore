"""Management REST API for FileStore records.

Endpoints (all require Authorization: Bearer <key>):

  GET    /api/filestores/                    — list accessible filestores
  POST   /api/filestores/                    — provision a new filestore
  POST   /api/filestores/register            — register an existing directory
  GET    /api/filestores/<uid>/              — inspect a single filestore
  PATCH  /api/filestores/<uid>/              — update mutable fields (owner only)
  DELETE /api/filestores/<uid>/              — delete record + backing storage (owner, ephemeral only)
  POST   /api/filestores/<uid>/deregister    — remove registry record only (owner only)
  GET    /api/filestores/<uid>/files/        — flat file tree
    POST   /api/filestores/<uid>/files/upload   — upload one binary file without overwrite
    GET    /api/filestores/<uid>/files/download — download one binary file

Access rule: a filestore is accessible when shared=True OR owner=request.auth_user.
"""
from __future__ import annotations

import json
import mimetypes
import shutil
from pathlib import Path

from django.conf import settings
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.http import content_disposition_header
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from filestores.creation import create_filestore_record, provision_local_filestore
from filestores.domain import validate_store_path
from filestores.models import FileStore, FileStoreKind
from filestores.registry import get_adapter

from .auth import require_api_key


TRANSFER_TOO_LARGE_ERROR = (
    "Files larger than 100 MB are not currently supported because file transfers "
    "are buffered in memory. This is a temporary limitation pending streaming "
    "upload and download support."
)


def _serialize(fs: FileStore) -> dict:
    return {
        "uid": fs.uid,
        "displayName": fs.display_name,
        "description": fs.description,
        "kind": fs.kind,
        "ref": fs.ref,
        "readOnly": fs.read_only,
        "shared": fs.shared,
        "ephemeral": fs.ephemeral,
        "owner": fs.owner.username if fs.owner else None,
        "createdAt": fs.created_at.isoformat(),
        "updatedAt": fs.updated_at.isoformat(),
    }


def _accessible_qs(user):
    return FileStore.objects.filter(Q(shared=True) | Q(owner=user))


def _owner_or_403(record, user):
    if record.owner_id != user.pk:
        return JsonResponse({"error": "Only the owner can perform this action."}, status=403)
    return None


def _transfer_too_large() -> JsonResponse:
    return JsonResponse({"error": TRANSFER_TOO_LARGE_ERROR}, status=413)


def _validate_transfer_path(path: str) -> JsonResponse | None:
    try:
        validate_store_path(path)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return None


# ---- List + Provision ----

@csrf_exempt
@require_api_key
@require_http_methods(["GET", "POST"])
def filestore_list(request: HttpRequest) -> JsonResponse:
    if request.method == "GET":
        records = list(_accessible_qs(request.auth_user).order_by("-updated_at"))
        return JsonResponse({"filestores": [_serialize(r) for r in records]})

    # POST — provision new
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    display_name = body.get("displayName", "").strip()
    if not display_name:
        return JsonResponse({"error": "'displayName' is required."}, status=400)

    record = provision_local_filestore(
        owner=request.auth_user,
        root=Path(settings.FILESTORE_ROOT),
        display_name=display_name,
        description=body.get("description", "").strip(),
        read_only=bool(body.get("readOnly", False)),
        shared=bool(body.get("shared", False)),
    )
    return JsonResponse(_serialize(record), status=201)


# ---- Register existing ----

@csrf_exempt
@require_api_key
@require_http_methods(["POST"])
def filestore_register(request: HttpRequest) -> JsonResponse:
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    ref = body.get("ref", "").strip()
    display_name = body.get("displayName", "").strip()
    kind = body.get("kind", FileStoreKind.LOCAL).strip()

    if not ref:
        return JsonResponse({"error": "'ref' is required."}, status=400)
    if not display_name:
        return JsonResponse({"error": "'displayName' is required."}, status=400)
    if kind not in FileStoreKind.values:
        return JsonResponse({"error": f"'kind' must be one of {FileStoreKind.values}."}, status=400)

    record = create_filestore_record(
        owner=request.auth_user,
        display_name=display_name,
        description=body.get("description", "").strip(),
        kind=kind,
        ref_for_uid=lambda _uid: ref,
        read_only=bool(body.get("readOnly", False)),
        shared=bool(body.get("shared", False)),
        ephemeral=False,
    )
    return JsonResponse(_serialize(record), status=201)


# ---- Detail: GET + PATCH + DELETE ----

@csrf_exempt
@require_api_key
@require_http_methods(["GET", "PATCH", "DELETE"])
def filestore_detail(request: HttpRequest, uid: str) -> JsonResponse:
    try:
        record = _accessible_qs(request.auth_user).get(uid=uid)
    except FileStore.DoesNotExist:
        return JsonResponse({"error": f"Filestore '{uid}' not found or not accessible."}, status=404)

    if request.method == "GET":
        return JsonResponse(_serialize(record))

    err = _owner_or_403(record, request.auth_user)
    if err:
        return err

    if request.method == "PATCH":
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON."}, status=400)

        if "displayName" in body:
            new_name = body["displayName"].strip()
            if not new_name:
                return JsonResponse({"error": "'displayName' cannot be empty."}, status=400)
            record.display_name = new_name
        if "description" in body:
            record.description = body["description"].strip()
        if "readOnly" in body:
            record.read_only = bool(body["readOnly"])
        if "shared" in body:
            record.shared = bool(body["shared"])
        record.save()
        return JsonResponse(_serialize(record))

    # DELETE — ephemeral only, wipes backing storage
    if not record.ephemeral:
        return JsonResponse(
            {"error": "Only ephemeral filestores can be deleted. Use /deregister to remove the registry entry only."},
            status=403,
        )
    ref_path = Path(record.ref)
    record.delete()
    if ref_path.exists() and record.kind == FileStoreKind.LOCAL:
        shutil.rmtree(ref_path, ignore_errors=True)
    return JsonResponse({"deleted": True})


# ---- Deregister ----

@csrf_exempt
@require_api_key
@require_http_methods(["POST"])
def filestore_deregister(request: HttpRequest, uid: str) -> JsonResponse:
    try:
        record = _accessible_qs(request.auth_user).get(uid=uid)
    except FileStore.DoesNotExist:
        return JsonResponse({"error": f"Filestore '{uid}' not found or not accessible."}, status=404)

    err = _owner_or_403(record, request.auth_user)
    if err:
        return err

    record.delete()
    return JsonResponse({"deregistered": True})


# ---- File tree ----

@csrf_exempt
@require_api_key
@require_http_methods(["GET"])
def filestore_files(request: HttpRequest, uid: str) -> JsonResponse:
    try:
        record = _accessible_qs(request.auth_user).get(uid=uid)
    except FileStore.DoesNotExist:
        return JsonResponse({"error": f"Filestore '{uid}' not found or not accessible."}, status=404)

    store = get_adapter(record)
    files = [
        {
            "path": e.path,
            "bytes": e.size,
            "mtimeMs": int(e.mtime * 1000),
            "type": "dir" if e.is_dir else "file",
        }
        for e in store.list()
    ]
    return JsonResponse({"files": files})


# ---- Binary file transfer ----

@csrf_exempt
@require_api_key
@require_http_methods(["POST"])
def filestore_file_upload(request: HttpRequest, uid: str) -> JsonResponse:
    try:
        record = _accessible_qs(request.auth_user).get(uid=uid)
    except FileStore.DoesNotExist:
        return JsonResponse({"error": f"Filestore '{uid}' not found or not accessible."}, status=404)

    if record.read_only:
        return JsonResponse({"error": f"Filestore '{uid}' is read-only."}, status=403)

    path = request.POST.get("path", "")
    uploaded_file = request.FILES.get("file")
    if not path:
        return JsonResponse({"error": "'path' is required."}, status=400)
    if uploaded_file is None:
        return JsonResponse({"error": "'file' is required."}, status=400)
    path_error = _validate_transfer_path(path)
    if path_error:
        return path_error
    if uploaded_file.size > settings.FILE_TRANSFER_MAX_BYTES:
        return _transfer_too_large()

    store = get_adapter(record)
    try:
        store.stat(path)
    except FileNotFoundError:
        pass
    else:
        return JsonResponse({"error": f"'{path}' already exists."}, status=409)

    content = uploaded_file.read()
    if len(content) > settings.FILE_TRANSFER_MAX_BYTES:
        return _transfer_too_large()
    try:
        store.create_whole(path, content)
    except FileExistsError:
        return JsonResponse({"error": f"'{path}' already exists."}, status=409)
    return JsonResponse({"path": path, "bytes": len(content)}, status=201)


@csrf_exempt
@require_api_key
@require_http_methods(["GET"])
def filestore_file_download(request: HttpRequest, uid: str) -> HttpResponse:
    try:
        record = _accessible_qs(request.auth_user).get(uid=uid)
    except FileStore.DoesNotExist:
        return JsonResponse({"error": f"Filestore '{uid}' not found or not accessible."}, status=404)

    path = request.GET.get("path", "")
    if not path:
        return JsonResponse({"error": "'path' is required."}, status=400)
    path_error = _validate_transfer_path(path)
    if path_error:
        return path_error

    store = get_adapter(record)
    try:
        entry = store.stat(path)
    except FileNotFoundError:
        return JsonResponse({"error": f"File '{path}' not found."}, status=404)
    if entry.is_dir:
        return JsonResponse({"error": f"'{path}' is a directory."}, status=400)
    if entry.size > settings.FILE_TRANSFER_MAX_BYTES:
        return _transfer_too_large()

    try:
        content = store.read_whole(path)
    except FileNotFoundError:
        return JsonResponse({"error": f"File '{path}' not found."}, status=404)
    if len(content) > settings.FILE_TRANSFER_MAX_BYTES:
        return _transfer_too_large()

    content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    response = HttpResponse(content, content_type=content_type)
    response.headers["Content-Disposition"] = content_disposition_header(
        True,
        Path(path).name,
    )
    return response