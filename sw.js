self.addEventListener('push', function(e) {
    const data = e.data ? e.data.json() : { title: 'AI Агент', body: 'Готово!' };
    e.waitUntil(
        self.registration.showNotification(data.title, {
            body: data.body,
            icon: 'https://cdn-icons-png.flaticon.com/512/4712/4712035.png',
            vibrate: [200, 100, 200]
        })
    );
});