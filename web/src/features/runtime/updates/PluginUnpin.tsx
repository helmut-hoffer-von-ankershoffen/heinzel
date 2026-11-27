// "Let it update again" for a plugin held to one version: the server previews the change (what stays, what may move),
// the person confirms that exact preview, and only then is the pin removed. The installed plugin is never touched.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/api/client";
import { Button } from "@/components/ui/button";
import { KEY } from "@/features/runtime/updates/autoUpdateApi";
import { t } from "@/i18n";

export function PluginUnpin({ name }: Readonly<{ name: string }>) {
  const client = useQueryClient();
  const confirm = useMutation({
    mutationFn: (digest: string) =>
      unwrap(api.POST("/system/plugin/unpin", { params: { query: { distribution: name, confirm: digest } } })),
    onSuccess: () => client.invalidateQueries({ queryKey: KEY }),
  });
  const preview = useMutation({
    mutationFn: () => unwrap(api.POST("/system/plugin/unpin", { params: { query: { distribution: name } } })),
    onSuccess: () => confirm.reset(),
  });
  if (confirm.isSuccess) return <p role="status">{confirm.data.message}</p>;
  const shown = preview.data;
  if (!shown?.confirmation)
    return (
      <div className="mt-1">
        <Button
          variant="ghost"
          size="sm"
          className="min-h-[44px] min-w-[44px]"
          disabled={preview.isPending}
          onClick={() => preview.mutate()}
        >
          {t("updates.unpin")}
        </Button>
        {(preview.isError || confirm.isError) && (
          <p role="alert" className="text-xs text-bad">
            {t("updates.unpinFailed")}
          </p>
        )}
      </div>
    );
  return (
    <div className="mt-1 space-y-1">
      <p className="text-xs">{shown.message}</p>
      <div className="flex flex-wrap gap-2">
        {confirm.isError ? (
          <Button
            size="sm"
            className="min-h-[44px] min-w-[44px]"
            disabled={preview.isPending}
            onClick={() => preview.mutate()}
          >
            {t("updates.unpinPreviewAgain")}
          </Button>
        ) : (
          <Button
            size="sm"
            className="min-h-[44px] min-w-[44px]"
            disabled={confirm.isPending}
            onClick={() => confirm.mutate(shown.confirmation ?? "")}
          >
            {t("updates.unpinConfirm", { name })}
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          className="min-h-[44px] min-w-[44px]"
          disabled={confirm.isPending || preview.isPending}
          onClick={() => {
            confirm.reset();
            preview.reset();
          }}
        >
          {t("updates.unpinCancel")}
        </Button>
      </div>
      {(preview.isError || confirm.isError) && (
        <p role="alert" className="text-xs text-bad">
          {t("updates.unpinFailed")}
        </p>
      )}
    </div>
  );
}
