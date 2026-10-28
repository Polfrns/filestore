from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Run the MCP server over stdio. All tool calls run as the given user."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True, help="Django username to bind tool calls to")

    def handle(self, *args, **options):
        from django.contrib.auth.models import User
        from transport.auth import _current_user
        from transport.server import mcp

        try:
            user = User.objects.get(username=options["username"])
        except User.DoesNotExist:
            raise CommandError(f"User '{options['username']}' not found.")

        # Set before mcp.run() — asyncio.run() copies the current context, so
        # anyio.to_thread.run_sync() (used for sync tools) inherits it too.
        _current_user.set(user)

        from kb.adapters.chroma_default import ChromaDefaultEmbeddingAdapter
        from kb.engine import set_embedding_port
        set_embedding_port(ChromaDefaultEmbeddingAdapter())

        self.stderr.write(f"MCP stdio server starting as '{user.username}'...\n")
        mcp.run(transport="stdio")