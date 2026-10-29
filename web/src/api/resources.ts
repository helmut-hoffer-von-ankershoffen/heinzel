import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, unwrap } from "./client";
import { keys } from "./queries";

function useVisible() {
  const [visible, setVisible] = useState(() => document.visibilityState !== "hidden");
  useEffect(() => {
    const update = () => setVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  return visible;
}

export function useFooterHealth() {
  const visible = useVisible();
  return useQuery({
    queryKey: keys.doctor,
    queryFn: ({ signal }) => unwrap(api.GET("/system/doctor", { signal })),
    enabled: visible,
    staleTime: 0,
    refetchInterval: visible ? 30_000 : false,
    refetchOnWindowFocus: false,
    retry: false,
  });
}
