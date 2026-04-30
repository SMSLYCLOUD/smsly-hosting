export function shouldShowAllNav() {
  return (
    process.env.NEXT_PUBLIC_SHOW_ALL_NAV === "true" ||
    process.env.NEXT_PUBLIC_ENABLE_TEST_NAV === "true" ||
    process.env.NODE_ENV !== "production"
  );
}
