'use client';

export function StormCloudBackground() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden="true">
      <div className="storm-base" />
      <div className="storm-cloud cloud-a" />
      <div className="storm-cloud cloud-b" />
      <div className="storm-cloud cloud-c" />
      <div className="storm-rain" />
      <div className="storm-lightning" />
      <div className="storm-vignette" />
    </div>
  );
}
