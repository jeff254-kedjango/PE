// Vitest setup: extends `expect` with jest-dom matchers (toBeInTheDocument, …)
// and runs once before the test suite (configured via vite.config.ts setupFiles).
import '@testing-library/jest-dom/vitest';

// jsdom has no Object-URL impl; components that preview picked files (e.g. TradeMediaUploader)
// call URL.createObjectURL/revokeObjectURL. Stub them so previews don't throw under test.
if (typeof URL.createObjectURL !== 'function') {
  URL.createObjectURL = () => 'blob:mock';
  URL.revokeObjectURL = () => {};
}
