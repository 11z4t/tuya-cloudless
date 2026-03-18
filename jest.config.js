/** @type {import('jest').Config} */
module.exports = {
  testEnvironment: "jest-environment-jsdom",
  testMatch: ["**/pairing_ui/tests/**/*.test.js"],
  // Silence the async IIFE errors from app.js during require()
  setupFiles: ["./pairing_ui/tests/setup.js"],
};
