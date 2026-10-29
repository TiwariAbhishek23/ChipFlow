"use client";

import { Button } from "@/components/ui/button";

export default function Error({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-background p-6">
      <section className="panel max-w-md">
        <div className="panel-header">
          <h1 className="text-base font-semibold">Application Error</h1>
        </div>
        <div className="panel-body space-y-4">
          <p className="text-sm text-muted-foreground">{error.message}</p>
          <Button onClick={reset}>Retry</Button>
        </div>
      </section>
    </main>
  );
}

