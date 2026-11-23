// Settings > Browser: the start page and the bookmarks bar, the settings `hz.browser_start_page` and
// `hz.browser_bookmarks_bar`, in the same rows as every other setting.
import { useConfig } from "@/api/queries";
import { Row } from "./SettingControls";

const KEYS = ["browser_start_page", "browser_bookmarks_bar"];

export function BrowserStartPageRow() {
  const config = useConfig();
  const rows = KEYS.flatMap((key) => config.data?.settings.find((s) => s.section === "hz" && s.key === key) ?? []);
  return (
    <>
      {rows.map((setting) => (
        <Row key={setting.key} setting={setting} />
      ))}
    </>
  );
}
