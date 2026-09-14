```javascript
// =========================================================
// PASSWORD SHOW / HIDE
// =========================================================

function togglePassword(inputId, button) {

    const input = document.getElementById(inputId);

    if (!input) {
        return;
    }

    if (input.type === "password") {

        input.type = "text";
        button.textContent = "HIDE";

    } else {

        input.type = "password";
        button.textContent = "SHOW";

    }
}


// =========================================================
// FORM SUBMIT ANIMATION
// =========================================================

document.addEventListener("DOMContentLoaded", () => {

    const forms = document.querySelectorAll("form");

    forms.forEach(form => {

        form.addEventListener("submit", () => {

            const button = form.querySelector(".primary-btn");

            if (button) {

                button.style.opacity = "0.75";
                button.style.pointerEvents = "none";
                button.textContent = "PROCESSING...";

            }

        });

    });

});
```
