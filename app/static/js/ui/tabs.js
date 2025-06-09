// ui/tabs.js
function changeTabViews(active_tab, inactive_tabs) {
    $(`#${active_tab}-view`).show();
    $(`#${active_tab}-tab`).addClass('active');

    for (let inactive_tab of inactive_tabs) {
        $(`#${inactive_tab}-view`).hide();
        $(`#${inactive_tab}-tab`).removeClass('active');
    }
}

function setTabClickEvents(tabs) {
    for (let tab of tabs) {
        $(`#${tab}-tab`).click(function (e) {
            e.preventDefault();
            const other_tabs = tabs.filter(t => t !== tab);
            changeTabViews(tab, other_tabs);
        });
    }
}

export { changeTabViews, setTabClickEvents };
