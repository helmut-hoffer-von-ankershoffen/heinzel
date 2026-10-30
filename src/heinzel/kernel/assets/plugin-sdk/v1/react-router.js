// SDK ABI 1: the host owns this runtime. Plugins never bundle another copy.
const runtime = globalThis[Symbol.for("heinzel.plugin-sdk.1")].router;
export default runtime;
export const Link = runtime.Link;
export const NavLink = runtime.NavLink;
export const Navigate = runtime.Navigate;
export const Outlet = runtime.Outlet;
export const useLocation = runtime.useLocation;
export const useNavigate = runtime.useNavigate;
export const useParams = runtime.useParams;
export const useSearchParams = runtime.useSearchParams;
export const useMatches = runtime.useMatches;
export const matchPath = runtime.matchPath;
export const generatePath = runtime.generatePath;
