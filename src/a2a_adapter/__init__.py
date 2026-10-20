"""Client-side A2A adapter for the standalone debate service.

The FinEvidence-RAG repository owns the A2A server implementation.  This
package intentionally avoids importing server-only modules at package import
time so the debate application depends only on the public client protocol.
"""

from .client import A2AEvidenceClient, A2AEvidenceClientError

__all__ = ["A2AEvidenceClient", "A2AEvidenceClientError"]
