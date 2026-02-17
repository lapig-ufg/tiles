import { NgModule } from '@angular/core';
import { ExtraOptions, RouterModule, Routes } from '@angular/router';
import { AppLayoutComponent } from './layout/app.layout.component';

const routerOptions: ExtraOptions = {
    anchorScrolling: 'enabled'
};

const routes: Routes = [
    {
        path: '', component: AppLayoutComponent,
        children: [
            { path: '', data: { breadcrumb: 'Grid Maps' }, loadChildren: () => import('./grid-map/map-grid.module').then(m => m.MapGridModule) },
            { path: 'imagery', data: { breadcrumb: 'Catálogo de Imagens' }, loadChildren: () => import('./image-catalog/image-catalog.module').then(m => m.ImageCatalogModule) },
            { path: 'embedding', data: { breadcrumb: 'Embedding Maps' }, loadChildren: () => import('./embedding-maps/embedding-maps.module').then(m => m.EmbeddingMapsModule) },
        ]
    },
    { path: 'auth', data: { breadcrumb: 'Auth' }, loadChildren: () => import('./demo/components/auth/auth.module').then(m => m.AuthModule) },
    { path: 'landing', loadChildren: () => import('./demo/components/landing/landing.module').then(m => m.LandingModule) },
    { path: 'notfound', loadChildren: () => import('./demo/components/notfound/notfound.module').then(m => m.NotfoundModule) },
    { path: '**', redirectTo: '/notfound' }
];

@NgModule({
    imports: [RouterModule.forRoot(routes, routerOptions)],
    exports: [RouterModule]
})
export class AppRoutingModule { }
