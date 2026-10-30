/** Stop the owned service and report an observed unclean exit before the shell exits. */
export async function stopWithReport(ownedCli, report) {
  const result = await ownedCli.stop();
  const unclean = result.outcome === "forced" || result.outcome === "abnormal" || (
    result.outcome === "already-exited" && (
      (result.code !== null && result.code !== 0) || result.signal !== null || result.processError
    )
  );
  if (unclean) report();
  return result;
}
