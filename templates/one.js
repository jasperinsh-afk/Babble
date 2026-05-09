document.addEventListener("DOMContentLoaded", () => {
  const login = document.getElementById("btnLogin");
  const reg = document.getElementById("btnRegister");

  if (login) {
    login.addEventListener("click", () => {
      // 实际项目可改为调用后端接口
      window.location.href = "/one";
    });
  }

  if (reg) {
    reg.addEventListener("click", () => {
      window.location.href = "/one";
    });
  }
});
