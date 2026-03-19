import "./app.css";
import { mount } from "svelte";
import App from "./App.svelte";
import RegistryAdmin from "./RegistryAdmin.svelte";

const Component = window.location.hash === "#/registry-admin" ? RegistryAdmin : App;

const app = mount(Component, {
  target: document.getElementById("app"),
});

export default app;
