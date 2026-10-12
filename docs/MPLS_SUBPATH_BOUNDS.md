# MPLS SubPath safety check

Declared SubPaths are length-delimited: the parser rejects missing four-byte lengths, undersized records, and records crossing the enclosing PlayList section. The contents remain unvalidated. Synthetic tests and real-device validation are still required before claiming compatibility.
