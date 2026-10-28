from django.db import models


class KBIndex(models.Model):
    """Tracks what has been ingested into ChromaDB, per file per filestore.

    Source of truth for staleness detection — mtime is stored here, not in
    ChromaDB metadata. One row per file per filestore; removed when the file
    is deleted from the backend (detected during the staleness diff).
    """

    filestore = models.ForeignKey(
        "filestores.FileStore",
        on_delete=models.CASCADE,
        related_name="kb_entries",
    )
    file_path = models.TextField()
    file_mtime = models.FloatField()
    ingested_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("filestore", "file_path")]

    def __str__(self) -> str:
        return f"{self.filestore.uid}:{self.file_path}"
