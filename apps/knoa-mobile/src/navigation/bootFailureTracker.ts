import * as SecureStore from "expo-secure-store";

const BOOT_FAILURE_KEY = "knoa.boot.failures.v1";
const SAFE_MODE_THRESHOLD = 3;

async function readCount(): Promise<number> {
  try {
    const raw = await SecureStore.getItemAsync(BOOT_FAILURE_KEY);
    const value = raw ? Number.parseInt(raw, 10) : 0;
    return Number.isSafeInteger(value) && value > 0 ? value : 0;
  } catch {
    return 0;
  }
}

async function writeCount(value: number): Promise<void> {
  try {
    await SecureStore.setItemAsync(BOOT_FAILURE_KEY, String(value));
  } catch {
    // Boot tracking must never break startup.
  }
}

/** Consecutive splash restore failures. Resets on any success. */
export async function shouldEnterSafeMode(): Promise<boolean> {
  return (await readCount()) >= SAFE_MODE_THRESHOLD;
}

export async function recordBootSuccess(): Promise<void> {
  await writeCount(0);
}

export async function recordBootFailure(): Promise<void> {
  await writeCount((await readCount()) + 1);
}
