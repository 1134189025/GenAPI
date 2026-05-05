export const MAX_IMAGE_BATCH_CONCURRENCY = 10;

export function resolveImageBatchConcurrencyLimit(value: number | null | undefined) {
  const numericValue = Math.floor(Number(value));
  if (!Number.isFinite(numericValue) || numericValue < 1) {
    return 1;
  }
  return Math.min(MAX_IMAGE_BATCH_CONCURRENCY, numericValue);
}

export async function runBoundedImageBatch<TItem>({
  items,
  concurrencyLimit,
  shouldContinue,
  runItem,
}: {
  items: TItem[];
  concurrencyLimit: number;
  shouldContinue: () => boolean | Promise<boolean>;
  runItem: (item: TItem, index: number) => Promise<void>;
}) {
  const normalizedLimit = resolveImageBatchConcurrencyLimit(concurrencyLimit);
  const workerCount = Math.min(normalizedLimit, items.length);
  let nextItemIndex = 0;

  const runWorker = async () => {
    while (await shouldContinue()) {
      const currentIndex = nextItemIndex;
      nextItemIndex += 1;
      const item = items[currentIndex];
      if (item === undefined) {
        return;
      }
      await runItem(item, currentIndex);
    }
  };

  const workers = Array.from({ length: workerCount }, () => runWorker());
  const settledWorkers = await Promise.allSettled(workers);
  const rejectedWorker = settledWorkers.find(
    (result): result is PromiseRejectedResult => result.status === "rejected",
  );
  if (rejectedWorker) {
    throw rejectedWorker.reason;
  }
}
