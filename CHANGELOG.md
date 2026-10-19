# Changelog

## Unreleased

### Changed

- Add an opt-in `reraise_exceptions` parameter to `powertools_handler`. When enabled,
  handler failures are logged and alerted before the original exception is re-raised so
  stream and asynchronous event sources can apply their configured failure handling.
  The default remains the existing 500 response, while SQS events continue to re-raise
  unconditionally.
