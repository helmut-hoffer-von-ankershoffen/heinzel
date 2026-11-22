// Settings > Browser: the start page, the setting `hz.browser_start_page`, in the same row as every other setting.
import { useConfig } from "@/api/queries";
import { Row } from "./SettingControls";

export function BrowserStartPageRow() {
  const config = useConfig();
  const setting = config.data?.settings.find((s) => s.section === "hz" && s.key === "browser_start_page");
  return setting ? <Row setting={setting} /> : null;
}
